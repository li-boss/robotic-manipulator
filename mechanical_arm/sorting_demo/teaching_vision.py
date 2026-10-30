"""不使用永久桌面标记的顶面候选和自然背景检查。"""
import cv2
import numpy as np


def top(frame,vision):
    if not vision.get('vision_reviewed'):raise ValueError('请先核对黄色HSV与最小面积')
    low,high=vision['colors']['yellow']
    hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
    mask=cv2.inRange(hsv,np.asarray(low),np.asarray(high))
    # 使用整幅画面，不沿用旧固定取物ROI。
    blobs=[c for c in cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[-2] if cv2.contourArea(c)>=vision['min_area']]
    if len(blobs)!=1:raise ValueError('整幅画面须恰好一个黄色木块')
    colored=cv2.inRange(hsv,np.array([0,60,40]),np.array([179,255,255]))
    others=[c for c in cv2.findContours(colored,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[-2] if cv2.contourArea(c)>=vision['min_area']]
    if len(others)!=1:raise ValueError('存在其他有色目标，无法可靠确认单块木块')
    edges=cv2.Canny(frame,60,160)
    candidates=[]
    for contour in list(cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)[-2])+blobs:
        quad=cv2.approxPolyDP(contour,.025*cv2.arcLength(contour,True),True)
        if len(quad)!=4 or not cv2.isContourConvex(quad) or cv2.contourArea(quad)<vision['min_area']:continue
        region=np.zeros(mask.shape,np.uint8);cv2.drawContours(region,[quad],-1,255,-1)
        area=cv2.countNonZero(region)
        if cv2.countNonZero(cv2.bitwise_and(mask,region))/area<.92:continue
        rect=cv2.minAreaRect(quad);width,height=sorted(rect[1])
        if width<=0 or height/width>2:continue
        polygon=quad.reshape(4,2).astype(float)
        # 顶面平面对角线交点，避免把侧面合并后的质心当作中心。
        p0,p1,p2,p3=polygon
        try:t=np.linalg.solve(np.column_stack([p2-p0,-(p3-p1)]),p1-p0)[0]
        except np.linalg.LinAlgError:continue
        center=p0+t*(p2-p0)
        descriptor=dict(area=float(cv2.contourArea(quad)),width=float(width),height=float(height),angle=float(rect[2]%90),
                        hue=float(np.median(hsv[:,:,0][region>0])))
        item=dict(pixel=center.tolist(),quad=polygon.tolist(),descriptor=descriptor)
        def same_outline(other):
            distances=np.linalg.norm(polygon[:,None,:]-np.asarray(other['quad'])[None,:,:],axis=2)
            return np.linalg.norm(center-other['pixel'])<2 and max(np.max(np.min(distances,axis=0)),np.max(np.min(distances,axis=1)))<=3
        # Canny的两侧边缘与JPEG颜色边缘可相差1～3像素；只合并同一四边轮廓。
        if not any(same_outline(x) for x in candidates):candidates.append(item)
    if len(candidates)!=1:raise ValueError('顶面中心不明确，请调整观察姿态、光照或阈值')
    return candidates[0]


def matches(found,samples):
    descriptor=found['descriptor']
    for sample in samples:
        reference=sample['descriptor']
        angle=abs(descriptor['angle']-reference['angle']);angle=min(angle,90-angle)
        hue=abs(descriptor['hue']-reference['hue']);hue=min(hue,180-hue)
        if all(abs(descriptor[key]/reference[key]-1)<=limit for key,limit in [('area',.25),('width',.2),('height',.2)]) and angle<=10 and hue<=10:return
    raise ValueError('木块外观、大小或朝向超出本单元样本范围')


def background(reference,current,vision):
    low,high=vision['colors']['yellow']
    exclude=cv2.inRange(cv2.cvtColor(current,cv2.COLOR_BGR2HSV),np.asarray(low),np.asarray(high))
    mask=cv2.bitwise_not(cv2.dilate(exclude,np.ones((21,21),np.uint8)))
    before=cv2.cvtColor(reference,cv2.COLOR_BGR2GRAY);after=cv2.cvtColor(current,cv2.COLOR_BGR2GRAY)
    points=cv2.goodFeaturesToTrack(before,maxCorners=100,qualityLevel=.03,minDistance=10,mask=mask)
    if points is None or len(points)<10:return dict(manual=True,message='场景变化需人工核对：背景自然特征不足')
    tracked,ok,_=cv2.calcOpticalFlowPyrLK(before,after,points,None)
    if tracked is None:return dict(manual=True,message='场景变化需人工核对：背景无法可靠跟踪')
    reverse,back_ok,_=cv2.calcOpticalFlowPyrLK(after,before,tracked,None)
    if reverse is None:return dict(manual=True,message='场景变化需人工核对：背景无法可靠跟踪')
    selected=(ok.reshape(-1)>0)&(back_ok.reshape(-1)>0)&(np.linalg.norm(reverse-points,axis=2).reshape(-1)<.5)
    if np.count_nonzero(selected)<8:return dict(manual=True,message='场景变化需人工核对：可靠背景特征不足')
    source=points[selected].reshape(-1,2);dest=tracked[selected].reshape(-1,2)
    matrix,inliers=cv2.estimateAffinePartial2D(source,dest,method=cv2.RANSAC,ransacReprojThreshold=1)
    if matrix is None or np.count_nonzero(inliers)<8:return dict(manual=True,message='场景变化需人工核对：背景匹配不足')
    drift=float(np.median(np.linalg.norm(source-dest,axis=1)))
    scale=float(np.hypot(matrix[0,0],matrix[1,0]));angle=abs(float(np.degrees(np.arctan2(matrix[1,0],matrix[0,0]))))
    if drift>2 or abs(scale-1)>.02 or angle>1:raise ValueError('相机或桌面背景发生变化，请重新核对示教')
    return dict(manual=False,message='自然背景检查通过',drift_px=drift)
