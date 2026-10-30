"""Fast candidate IK for the validated course chain; every target rechecked by FK."""
import itertools
import math
import numpy as np


def batch_matrices(robot,qs):
    qs=np.asarray(qs,float).reshape(-1,5);count=len(qs)
    matrix=np.repeat(np.eye(4)[None],count,axis=0)
    poses=[matrix.copy()]
    for i,(origin,axis,_,_) in enumerate(robot.joints):
        if not np.array_equal(axis,[0,0,1]):raise ValueError('快速逆解不适用于当前关节轴')
        theta=np.radians((qs[:,i]-robot.offset[i])*robot.sign[i]);c=np.cos(theta);s=np.sin(theta)
        turn=np.repeat(np.eye(4)[None],count,axis=0)
        turn[:,0,0]=c;turn[:,0,1]=-s;turn[:,1,0]=s;turn[:,1,1]=c
        matrix=(matrix@origin)@turn
        poses.append(matrix.copy())
    return np.stack(poses,axis=1)


def batch_fk(robot,qs):
    return batch_matrices(robot,qs)[:,-1]@robot.tool


def residual(robot,qs,target,desired,approach):
    m=batch_fk(robot,qs);jaw=(m[:,:3,:3]@robot.jaw_axis)[:,:2]
    lengths=np.linalg.norm(jaw,axis=1)
    if np.any(lengths<1e-8):raise ValueError('夹爪方向投影退化')
    return np.concatenate((m[:,:3,3]-target,.06*(m[:,:3,2]-approach),.04*(jaw/lengths[:,None]-desired)),axis=1)


def refine(robot,q,target,desired,approach,bounds,iterations=5):
    q=np.asarray(q,float).copy()
    if np.any(q<bounds[:,0]) or np.any(q>bounds[:,1]):return None
    for _ in range(iterations):
        probes=np.repeat(q[None],6,axis=0);probes[1:]+=np.eye(5)*.001
        errors=residual(robot,probes,target,desired,approach);e=errors[0]
        if np.linalg.norm(e)<1e-9:break
        # A search seed need not converge to machine precision. Final integer
        # candidates still pass the unchanged exact FK acceptance checks.
        if (np.linalg.norm(e[:3])<=min(.001,robot.cfg['max_position_error_mm']/4000) and
                np.linalg.norm(e[3:6])/.06<=.017 and
                np.linalg.norm(e[6:])/.04<=2*math.sin(math.radians(robot.cfg['max_direction_error_deg']/4)/2)):
            break
        jac=((errors[1:]-e)/.001).T;free=bounds[:,0]!=bounds[:,1];delta=np.zeros(5)
        for _active in range(6):
            jf=jac[:,free]
            if jf.shape[1]==0:break
            delta[:]=0;delta[free]=-np.linalg.solve(jf.T@jf+np.eye(jf.shape[1])*1e-7,jf.T@e)
            outward=((q<=bounds[:,0]+1e-8)&(delta<0))|((q>=bounds[:,1]-1e-8)&(delta>0))
            if not np.any(outward&free):break
            free[outward]=False
        accepted=False
        for scale in (1,.5,.25,.125,.0625,.03125):
            proposal=q+scale*delta
            if np.any(proposal<bounds[:,0]) or np.any(proposal>bounds[:,1]):continue
            if np.linalg.norm(residual(robot,[proposal],target,desired,approach)[0])<np.linalg.norm(e)-1e-12:
                q=proposal;accepted=True;break
        if not accepted:break
    return q


def candidates(robot,xyz,yaw,tilt,reference,current,held_wrist=None):
    """Geometry seeds + short correction; no clipping, modulo or altered limits."""
    from core import arm
    target=np.asarray(xyz,float)/1000;theta=math.radians(tilt)
    diagnostic=dict(min_position_error_mm=None)
    if not hasattr(robot,'_guided_ik_diagnostics'):robot._guided_ik_diagnostics={}
    robot._guided_ik_diagnostics[(tuple(xyz),float(yaw),tilt,held_wrist)]=diagnostic
    if not 0<=tilt<90:raise ValueError('快速逆解接近倾角须在0到90度之间')
    radial=np.linalg.norm(target[:2])
    if radial<1e-9:raise ValueError('目标接近底座轴，方向退化')
    approach=np.r_[target[:2]/radial*math.sin(theta),-math.cos(theta)]
    bounds=np.asarray([robot.arm['joint_limits'][str(i)] for i in range(1,6)],float)
    if held_wrist is not None:
        arm.angle(robot.arm,5,held_wrist);bounds[4]=[held_wrist,held_wrist]
    l1=np.linalg.norm(robot.joints[2][0][:3,3]);l2=np.linalg.norm(robot.joints[3][0][:3,3])
    l3=abs(robot.joints[4][0][0,3]);side=abs(robot.joints[4][0][1,3]);tool=robot.tool[2,3]
    a=math.atan2(-target[0],target[1]);S=-math.pi+theta
    yy=radial-(tool+l3)*math.sin(theta)-side*math.cos(theta)
    zz=target[2]+tool*math.cos(theta)-sum(j[0][2,3] for j in robot.joints[:2])+l3*math.cos(theta)-side*math.sin(theta)
    cosine=(yy*yy+zz*zz-l1*l1-l2*l2)/(2*l1*l2)
    if not -1<=cosine<=1:
        if robot.cfg['max_position_error_mm']<2:return []
        allowance=robot.cfg['max_position_error_mm']/1000+.035*(abs(tool)+l3+side)+np.linalg.norm(robot.tool[:2,3])
        if math.hypot(yy,zz)>l1+l2+allowance:return []
    choices={}
    for yaw_offset in (0,90,180,270):
        angle=math.radians(yaw+yaw_offset);desired=np.array([math.cos(angle),math.sin(angle)])
        relative=angle-a;wrist=math.atan2(math.sin(relative)/math.cos(S),math.cos(relative))
        roots=[]
        for elbow in ((math.acos(cosine),-math.acos(cosine)) if -1<=cosine<=1 else ()):
            shoulder=math.atan2(-yy,zz)-math.atan2(l2*math.sin(elbow),l1+l2*math.cos(elbow))
            for winding in (-1,0,1):
                q=robot.offset+np.degrees([a,shoulder,elbow,S-shoulder-elbow,wrist+2*math.pi*winding])/robot.sign
                if held_wrist is not None:q[4]=held_wrist
                if np.any(q<bounds[:,0]) or np.any(q>bounds[:,1]):continue
                roots.append((q,5))
        if robot.cfg['max_position_error_mm']>=2:
            # Wider integer tolerance can admit a legitimate boundary solution
            # absent from exact analytic roots. Start from the already legal
            # nine-point pose; never clip an out-of-range analytic angle.
            guided=np.asarray(reference,float).copy()
            if held_wrist is not None:guided[4]=held_wrist
            if np.all(guided>=bounds[:,0]) and np.all(guided<=bounds[:,1]):roots.append((guided,16))
        searches=[(root,iterations,target) for root,iterations in roots]
        if robot.cfg['max_position_error_mm']>=2:
            offset=robot.cfg['max_position_error_mm']/2000
            for root,iterations in roots:
                for delta in (offset,-offset):
                    sample=target.copy();sample[2]+=delta;searches.append((root,iterations,sample))
        for root,iterations,sample_target in searches:
            try:q=refine(robot,root,sample_target,desired,approach,bounds,iterations)
            except (ValueError,np.linalg.LinAlgError):continue
            if q is None:continue
            grids=[]
            for joint,value in enumerate(q):
                values={math.floor(value),math.ceil(value)}
                if robot.cfg['max_position_error_mm']>=2 and joint in (0,4):values|={math.floor(value)-1,math.ceil(value)+1}
                grids.append(sorted(values))
            integers=np.asarray(list(itertools.product(*grids)),int)
            valid=np.all((integers>=bounds[:,0])&(integers<=bounds[:,1]),axis=1)
            physical=np.radians((integers-robot.offset)*robot.sign)
            valid &= np.all((physical>=np.asarray([j[2] for j in robot.joints]))&(physical<=np.asarray([j[3] for j in robot.joints])),axis=1)
            integers=integers[valid]
            if not len(integers):continue
            matrices=batch_fk(robot,integers);jaw=(matrices[:,:3,:3]@robot.jaw_axis)[:,:2];length=np.linalg.norm(jaw,axis=1)
            error=np.linalg.norm(matrices[:,:3,3]-target,axis=1)*1000
            lowest=float(np.min(error))
            if diagnostic['min_position_error_mm'] is None or lowest<diagnostic['min_position_error_mm']:diagnostic['min_position_error_mm']=lowest
            cosine_yaw=(jaw@desired)/np.maximum(length,1e-30)
            direction=np.degrees(np.arccos(np.clip(cosine_yaw,-1,1)))
            valid=(length>1e-8)&(error<=robot.cfg['max_position_error_mm'])&(direction<=robot.cfg['max_direction_error_deg'])&(np.linalg.norm(matrices[:,:3,2]-approach,axis=1)<=.035)
            for index in np.flatnonzero(valid):
                integer=tuple(int(v) for v in integers[index])
                # Real target validation stays at the original strict FK entry.
                robot.fk(list(integer),True)
                score=float(error[index]+.1*direction[index]+.05*np.linalg.norm(integers[index]-reference))
                if integer not in choices or choices[integer]['score']>score:
                    choices[integer]=dict(q=list(integer),tilt=tilt,score=score,error_mm=float(error[index]))
    return sorted(choices.values(),key=lambda c:(c['score'],c['q']))
