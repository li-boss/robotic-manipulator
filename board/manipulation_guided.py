"""Nine-point guided candidate and whole-task backtracking, with original sweep checks."""
import math
import numpy as np

from manipulation_reference import reference_seed,validate_reference
from manipulation_ik import candidates,batch_fk,batch_matrices


class GuidedSearch:
    def __init__(self,cfg,arms,reference,progress=None):
        from manipulation_geometry import ManipulationRobot
        validate_reference(reference,cfg,arms)
        self.cfg,self.arms,self.reference,self.progress=cfg,arms,reference,progress
        self.robot=ManipulationRobot(cfg,arms)
        self.robot._batch_sweeps=True
        self.candidate_cache={};self.path_cache={}
        self.decisions=[];self.chosen=[];self.stages=[]

    def tilts(self,reference,tilt_hint=None,force_tilt=False):
        pose,_=self.robot.fk(reference,False)
        reference_tilt=math.degrees(math.acos(float(np.clip(-pose[2,2],-1,1))))
        preferred=reference_tilt if tilt_hint is None else tilt_hint
        result=[]
        for value in ([tilt_hint] if force_tilt else [preferred,reference_tilt,45.,60.,30.,75.,15.,0.]):
            if value is not None and 0<=value<85 and not any(abs(value-t)<1e-7 for t in result):result.append(float(value))
        return result

    def options(self,xyz,yaw,tilt,reference,current,held_wrist):
        key=(tuple(xyz),float(yaw),tilt,tuple(reference),held_wrist)
        if key not in self.candidate_cache:self.candidate_cache[key]=candidates(self.robot,xyz,yaw,tilt,reference,current,held_wrist)
        return sorted(self.candidate_cache[key],key=lambda c:(c['score']+.001*np.linalg.norm(np.asarray(c['q'])-current),c['q']))

    def precheck_place(self,located):
        self.located=located
        if located['task_mode']!='pick_place':return
        # Wider budgets include legal pose/height alternatives. Check their
        # placement feasibility lazily at each actual grasp candidate instead
        # of expanding the complete wrist/pitch Cartesian product up front.
        if self.cfg['max_position_error_mm']>=2:return
        xyz=[*located['destination_xy_mm'],self.cfg['table_z_mm']+self.cfg['transfer_height_mm']]
        source=reference_seed(self.reference,xyz[:2],'above');reference=source['angles']
        target_base=math.degrees(math.atan2(-xyz[0],xyz[1]))
        # Enumerate every possible wrist/pitch produced by this planner's grasp
        # candidates. A precheck must cover a superset of subsequent choices,
        # rather than rejecting a placement using one guessed grasp branch.
        pickup=[*located['xyz_mm'][:2],self.cfg['table_z_mm']+self.cfg['grasp_height_mm']]
        grasp=reference_seed(self.reference,pickup[:2],'grasp')['angles'];pairs=set()
        for tilt in self.tilts(grasp):
            for option in self.options(pickup,located['edge_yaw_deg'],tilt,grasp,grasp,None):
                pose,_=self.robot.fk(option['q'])
                actual=math.degrees(math.acos(float(np.clip(-pose[2,2],-1,1))))
                base=batch_matrices(self.robot,[option['q']])[0,1]
                base_yaw=math.degrees(math.atan2(base[1,0],base[0,0]))
                jaw=pose[:3,:3]@self.robot.jaw_axis
                relative_yaw=math.degrees(math.atan2(jaw[1],jaw[0]))-base_yaw
                pairs.add((option['q'][4],actual,relative_yaw))
        if not pairs:return  # Let the grasp stage explain its own failure.
        errors=[]
        for wrist,pitch,relative_yaw in sorted(pairs):
            yaw=relative_yaw+target_base
            for tilt in self.tilts(reference,pitch):
                if self.options(xyz,yaw,tilt,reference,reference,wrist):return
                diagnostic=self.robot._guided_ik_diagnostics.get((tuple(xyz),float(yaw),tilt,wrist),{})
                if diagnostic.get('min_position_error_mm') is not None:errors.append(diagnostic['min_position_error_mm'])
        detail=('；本次枚举整数候选的最小位置误差约'+str(round(min(errors),2))+'毫米' if errors else '')
        raise ValueError('放置点上方：九点引导的有限整数候选未满足位置/方向条件（当前逆解位置门槛'+str(self.cfg['max_position_error_mm'])+'毫米）'+detail)

    def grasp_endpoints(self,located,first=False):
        """取物点合法整数夹取终点（不含路径）；与move中夹取阶段的终点条件相同。first=True找到一个即返回。"""
        pickup=[*located['xyz_mm'][:2],self.cfg['table_z_mm']+self.cfg['grasp_height_mm']]
        reference=reference_seed(self.reference,pickup[:2],'grasp')['angles']
        margin=self.robot._cad_reach*math.radians(1.25);errors=[];passed=[]
        for tilt in self.tilts(reference):
            for option in self.options(pickup,located['edge_yaw_deg'],tilt,reference,reference,None):
                end=option['q']
                try:self.robot.collision(end,None,margin)
                except ValueError as exc:errors.append(str(exc));continue
                pose,_=self.robot.fk(end)
                if not self.cfg['table_z_mm']<pose[2,3]*1000<self.cfg['table_z_mm']+self.cfg['block_size_mm'][2]:
                    errors.append('模型夹持中心不在方块高度内');continue
                if located['task_mode']=='pick_place' and not self.drop_feasible(end,pose,margin):
                    errors.append('该抓取姿态不能在放置点保持方块方向下降');continue
                passed.append(end)
                if first:return passed,errors
        return passed,errors

    def precheck_grasp(self,located):
        """快速拒绝：没有任何夹取终点能通过时，不再对前后阶段做回溯搜索。

        这是move中夹取阶段条件的子集检查（同一缓存候选、同一碰撞与高度判据），
        只能提前得出“必然失败”，不会放行move本身不放行的计划。"""
        passed,errors=self.grasp_endpoints(located,first=True)
        if passed:return
        r=float(np.linalg.norm(located['xyz_mm'][:2]))
        if '该抓取姿态不能在放置点保持方块方向下降' in errors:
            reason='取物点可以夹取，但按各可行夹持方向都无法在所选放置点平放方块；请把放置点选得离底座近一些，或更靠近取物点所在方向'
        elif errors:reason='所有夹取姿态均不满足：'+errors[-1]
        else:reason='该距离没有满足位置、方向和关节范围的整数夹取姿态'
        raise ValueError(f'接近抓取：取物点距底座约{r:.0f}毫米，{reason}（快速预检，未进行路径搜索）')

    def drop_feasible(self,end,pose,margin):
        located=self.located
        goal=self.drop_goal(end,pose);drop_reference=reference_seed(self.reference,located['destination_xy_mm'],'grasp')['angles']
        desired=np.r_[located['destination_xy_mm'],self.cfg['table_z_mm']+self.cfg['block_size_mm'][2]/2+self.cfg['release_gap_mm']]
        for drop_option in self.options(goal['xyz'],goal['yaw'],goal['tilt'],drop_reference,end,end[4]):
            try:self.robot.collision(drop_option['q'],goal['attachment'],margin)
            except ValueError:continue
            released=self.robot.fk(drop_option['q'])[0]@goal['attachment']
            if (np.linalg.norm(released[:3,3]*1000-desired)<=self.cfg['max_position_error_mm'] and
                    np.linalg.norm(released[:3,2]-[0,0,1])<=math.sin(math.radians(self.cfg['max_direction_error_deg']))):
                return True
        return False

    def begin(self,decisions):
        self.decisions=decisions;self.chosen=[];self.stages=[]

    def drop_goal(self,angles,pose=None):
        """Place the attached block at the requested point, compensating the
        actual grasp offset rather than assuming a perfect zero-error grasp."""
        from robot_model import transform,rotation
        if pose is None:pose=self.robot.fk(angles)[0]
        located=self.located;block=self.cfg['block_size_mm'];z=self.cfg['table_z_mm']
        source=np.r_[located['xyz_mm'][:2],z+block[2]/2]
        initial=transform(source/1000,[0,0,math.radians(located['edge_yaw_deg'])])
        attachment=np.linalg.inv(pose)@initial
        base=batch_matrices(self.robot,[angles])[0,1]
        base_yaw=math.atan2(base[1,0],base[0,0])
        dest=located['destination_xy_mm'];desired=np.r_[dest,z+block[2]/2+self.cfg['release_gap_mm']]
        next_base=math.atan2(-dest[0],dest[1])
        for _ in range(4):
            orientation=rotation([0,0,1],next_base-base_yaw)@pose[:3,:3]
            xyz=desired-orientation@attachment[:3,3]*1000
            next_base=math.atan2(-xyz[0],xyz[1])
        jaw=orientation@self.robot.jaw_axis
        return dict(xyz=xyz.tolist(),yaw=math.degrees(math.atan2(jaw[1],jaw[0])),
            tilt=math.degrees(math.acos(float(np.clip(-orientation[2,2],-1,1)))),attachment=attachment)

    def move(self,label,xyz,current,yaw,carried,held_wrist=None,tilt_hint=None,force_tilt=False):
        index=len(self.chosen);wanted=self.decisions[index] if index<len(self.decisions) else 0
        stage='grasp' if label in ('接近抓取','下降放置') else 'above'
        reference_xy=self.located['destination_xy_mm'] if label=='下降放置' and hasattr(self,'located') else xyz[:2]
        source=reference_seed(self.reference,reference_xy,stage);reference=source['angles']
        tilts=self.tilts(reference,tilt_hint,force_tilt)
        margin=self.robot._cad_reach*math.radians(1.25)
        self.robot.collision(current,carried,margin)
        errors=[];available=0;seen=set()
        for tilt in tilts:
            if self.progress:self.progress('九点参考：'+label+'，求解 '+str(round(tilt,1))+'° 候选')
            options=self.options(xyz,yaw,tilt,reference,current,held_wrist)
            for option in options:
                end=option['q']
                if tuple(end) in seen:continue
                seen.add(tuple(end))
                try:self.robot.collision(end,carried,margin)
                except ValueError as exc:errors.append(str(exc));continue
                pose,_=self.robot.fk(end)
                if label=='接近抓取':
                    if not self.cfg['table_z_mm']<pose[2,3]*1000<self.cfg['table_z_mm']+self.cfg['block_size_mm'][2]:
                        errors.append('模型夹持中心不在方块高度内');continue
                    located=getattr(self,'located',None)
                    if located and located['task_mode']=='pick_place' and not self.drop_feasible(end,pose,margin):
                        errors.append('该抓取姿态不能在放置点保持方块方向下降');continue
                path_key=(tuple(current),tuple(end),None if carried is None else tuple(np.asarray(carried).ravel()))
                if path_key not in self.path_cache:
                    if self.progress:self.progress('九点参考：'+label+'，验证同步扫掠路径')
                    try:self.path_cache[path_key]=self.robot.grouped_transition(current,end,carried)
                    except ValueError as exc:self.path_cache[path_key]=str(exc)
                commands=self.path_cache[path_key]
                if isinstance(commands,str):errors.append(commands);continue
                if available<wanted:available+=1;continue
                self.chosen.append(available)
                pose,_=self.robot.fk(end);actual_tilt=math.degrees(math.acos(float(np.clip(-pose[2,2],-1,1))))
                self.stages.append(dict(label=label,source=source,tilt_deg=actual_tilt,requested_tilt_deg=tilt,angles=end,position_error_mm=option['error_mm']))
                return commands,end,actual_tilt
        incompatible='该抓取姿态不能在放置点保持方块方向下降'
        if label=='接近抓取' and incompatible in errors:
            reason='取物点可以夹取，但按各可行夹持方向都无法在所选放置点平放方块；请把放置点选得离底座近一些，或更靠近取物点所在方向'
        elif errors:reason=errors[-1]
        else:
            values=[d['min_position_error_mm'] for key,d in getattr(self.robot,'_guided_ik_diagnostics',{}).items()
                    if key[0]==tuple(xyz) and d.get('min_position_error_mm') is not None]
            reason='有限候选未满足位置、方向、整数角度及配置范围（位置容差'+str(self.cfg['max_position_error_mm'])+'毫米，方向容差'+str(self.cfg['max_direction_error_deg'])+'度）'
            if values:reason+='；本次整数候选最小位置误差约'+str(round(min(values),2))+'毫米'
        raise ValueError(label+'：九点引导候选未通过；'+reason)

    def summary(self):
        return dict(schema='nine_point_guided_v1',version=self.reference['version'],sample_count=9,
            baseline_frame_id=self.reference['baseline_frame_id'],
            cards_sha256=self.reference['source_cards_sha256'],states_sha256=self.reference['source_states_sha256'],
            coordinate_source='旧九点真实观察姿态 + 当前相机标定 + 方块顶面平面',
            source_type='user_report',stages=self.stages,
            position_tolerance_mm=self.cfg['max_position_error_mm'],
            note='九点只作初值/分支参考；全部目标重新计算和检查，未替代真实抓放验收')


def build_guided(cfg,arms,target,require_path,reference,progress=None):
    from manipulation_geometry import _build_plan,locate,readiness
    ready=readiness(cfg,arms,require_path)
    if not ready['ready']:raise ValueError('；'.join(x['reason'] for x in ready['issues']))
    from manipulation_geometry import check_reach
    check_reach(cfg,arms,locate(cfg,target))
    search=GuidedSearch(cfg,arms,reference,progress);decisions=[];last=None
    if progress:progress('九点参考：预检放置点的整数角度可达候选')
    located=locate(cfg,target)
    search.precheck_place(located)
    search.located=located
    if progress:progress('九点参考：快速预检取物点的合法夹取姿态')
    search.precheck_grasp(located)
    for attempt in range(32):
        search.begin(decisions)
        try:
            plan=_build_plan(cfg,arms,target,require_path,0,progress,guided=search)
            plan['reference']=search.summary()
            return plan
        except ValueError as exc:
            last=exc
            if not search.chosen:break
            decisions=search.chosen.copy();decisions[-1]+=1
            if progress:progress('九点参考：后续阶段未通过，回退到上一合法候选（'+str(attempt+1)+'）')
    raise ValueError('九点引导的有限完整计划未通过：'+str(last))
