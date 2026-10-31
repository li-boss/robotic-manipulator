"""Local preview markers only; boxes never restrict either detector's input."""
from emotion_interaction.smile_policy import DEFAULT_SMILE_THRESHOLD

HAND_COLOR = (255, 220, 0)  # BGR cyan
FACE_COLOR = (0, 255, 0)


def hand_status(prediction, threshold=.8):
    count = prediction.get("hand_count", 0)
    if count == 0:
        return "NO_HAND"
    if count != 1:
        return "MULTIPLE_HANDS"
    if prediction.get("gesture") is None:
        return "NON_TARGET_GESTURE"
    if prediction.get("confidence", 0) < threshold:
        return "LOW_CONFIDENCE"
    return "DETECTED"


def draw_hand_boxes(frame, prediction):
    import cv2
    h, w = frame.shape[:2]
    for index, box in enumerate(prediction.get("hand_boxes", [])):
        x1,y1,x2,y2 = [int(v*scale) for v,scale in zip(box,(w,h,w,h))]
        x1,x2 = max(0,min(w-1,x1)),max(0,min(w-1,x2))
        y1,y2 = max(0,min(h-1,y1)),max(0,min(h-1,y2))
        if x2 <= x1 or y2 <= y1:
            continue
        cv2.rectangle(frame,(x1,y1),(x2,y2),HAND_COLOR,2)
        cv2.putText(frame,f"HAND {index+1}",(x1,max(15,y1-5)),
                    cv2.FONT_HERSHEY_SIMPLEX,.5,HAND_COLOR,1)


def draw_combined_overlay(frame, record, *, smile_threshold=DEFAULT_SMILE_THRESHOLD,
                          gesture_threshold=.8, robot_connected=False):
    import cv2
    face, hand = record["smile"],record["gesture"]
    box = face.get("face_box")
    if box:
        h,w = frame.shape[:2]
        cv2.rectangle(frame,(int(box[0]*w),int(box[1]*h)),
                      (int(box[2]*w),int(box[3]*h)),FACE_COLOR,2)
    draw_hand_boxes(frame,hand)
    face_state = face["status"]
    if face_state == "FACE_DETECTED":
        face_state = "SMILE" if face["smile_score"] >= smile_threshold else "NO_SMILE"
    state = hand_status(hand,gesture_threshold)
    stale = record["processed_at"]-record["captured_at"] > 500
    rows = [(f"Face: {face_state} {face['smile_score']:.2f} / {smile_threshold:.2f}",FACE_COLOR),
            (f"Hand: {hand['raw_label']} {hand['confidence']:.2f} / {gesture_threshold:.2f}",HAND_COLOR),
            (f"Hand status: {state}",HAND_COLOR),
            (f"Processing {record.get('processing_ms',0):.0f} ms | {'STALE: triggers blocked' if stale else 'frame fresh'}",(0,200,255)),
            (f"Full-frame detection | {'JJT DIRECT' if robot_connected else 'LOCAL ONLY'} | Q: quit",(255,255,255))]
    # Dark banner keeps diagnostics readable; drawn only AFTER both inferences.
    cv2.rectangle(frame,(0,0),(frame.shape[1]-1,132),(25,25,25),-1)
    for i,(text,color) in enumerate(rows):
        cv2.putText(frame,text,(10,23+i*25),cv2.FONT_HERSHEY_SIMPLEX,.53,color,1)
