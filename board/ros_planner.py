"""ROS2纯计算规划节点；没有Controller、Arm_Lib或舵机接口。"""
import hashlib
import json
from pathlib import Path

from manipulation_geometry import build_plan as manipulation_build
from paths import BOARD, MODELS, RULE_FILES


def main():
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String
    for p in [*RULE_FILES,Path(__file__),BOARD/'manipulation_geometry.py',BOARD/'robot_model.py',
              BOARD/'cad_geometry.py',MODELS/'manipulation/cad_profile.json']:
        print('本次核对',p.name,hashlib.sha256(p.read_bytes()).hexdigest()[:16],flush=True)
    print('实际参数：ROS2纯计算规划服务；不连接或驱动机械臂',flush=True)
    rclpy.init()
    class Planner(Node):
        def __init__(self):
            super().__init__('jjt_auto_planner')
            self.publisher=self.create_publisher(String,'/jjt/auto_plan/response',10)
            self.subscription=self.create_subscription(String,'/jjt/auto_plan/request',self.receive,10)
        def receive(self,msg):
            result={'id':None}
            try:
                if len(msg.data)>65536:raise ValueError('请求过大')
                request=json.loads(msg.data);result['id']=request['id']
                result['input_hash']=hashlib.sha256(json.dumps(request['input'],sort_keys=True).encode()).hexdigest()
                args=request['input']
                if args.get('operation')=='health':
                    result['plan']=dict(service='jjt_auto_planner',hardware=False,
                        strategies=['manipulation'],
                        model_sha256=hashlib.sha256((MODELS/'course/dofbot.urdf').read_bytes()).hexdigest())
                elif args.get('operation')=='manipulation':
                    result['plan']=manipulation_build(args['setup'],args['arms'],args['target'],args.get('require_path',True),reference=args.get('reference'))
                else:
                    raise ValueError('未知规划请求')
            except Exception as exc:result['error']=str(exc)
            outgoing=String();outgoing.data=json.dumps(result,ensure_ascii=False);self.publisher.publish(outgoing)
    node=Planner()
    try:rclpy.spin(node)
    finally:node.destroy_node();rclpy.shutdown()


if __name__=='__main__':main()
