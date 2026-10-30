"""Section 3.4 durable fake sink: acknowledges only after SQLite commit."""
import json
import sqlite3


class FakeEventSink:
    def __init__(self, path):
        self.connection=sqlite3.connect(str(path))
        self.connection.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.connection.commit()
        self.online=True

    def receive(self,event,request_id="mock-event-request"):
        if not self.online:
            return 503,{"code":"STORE_UNAVAILABLE","message":"mock disconnected", "retryable":True,"request_id":request_id}
        required={"event_id","task_id","seq","occurred_at","event_type","device_state"}
        if not required <= set(event) or set(event)-required-{"reason","attempt"}:
            raise ValueError("INVALID_EVENT_FIELDS")
        if (not all(isinstance(event[k],str) and event[k] for k in ("event_id","task_id"))
                or type(event["seq"]) is not int or event["seq"]<1
                or type(event["occurred_at"]) is not int or event["occurred_at"]<0
                or event["event_type"] not in {"ACCEPTED","STARTED","PICKED","PLACED","SUCCEEDED","FAILED","STOPPED"}
                or event["device_state"] not in {"IDLE","BUSY","HOLDING","FAULT","STOPPING","STOPPED"}):
            raise ValueError("INVALID_EVENT_VALUE")
        body=json.dumps(event,sort_keys=True,ensure_ascii=False)
        row=self.connection.execute("SELECT body FROM events WHERE event_id=?",(event["event_id"],)).fetchone()
        if row:
            if row[0]!=body:
                return 409,{"code":"EVENT_ID_CONFLICT","message":"same ID has different content","retryable":False,"request_id":request_id}
            return 202,{"accepted":True,"duplicate":True}
        with self.connection:
            self.connection.execute("INSERT INTO events VALUES (?,?)",(event["event_id"],body))
        return 202,{"accepted":True,"duplicate":False}

    def close(self):
        self.connection.close()
