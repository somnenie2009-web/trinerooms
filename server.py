"""Short-lived collaboration rooms. Run a single instance behind HTTPS."""
import json
import os
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOMS={}; LOCK=threading.RLock(); TTL=120; MAX_ROOMS=100
REQUESTS=deque(); CREATES=deque()

def clean():
    now=time.monotonic()
    for key,room in list(ROOMS.items()):
        if now-room['last']>TTL:del ROOMS[key]

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup(); self.connection.settimeout(10)
    def log_message(self,*args):pass
    def reply(self,status,payload):
        raw=json.dumps(payload,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers()
        try:self.wfile.write(raw)
        except OSError:pass
    def do_GET(self):
        if self.path=='/health':return self.reply(200,{'ok':True})
        self.reply(404,{'error':'not_found'})
    def do_POST(self):
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 1<=n<=65536:raise ValueError()
            data=json.loads(self.rfile.read(n))
            if not isinstance(data,dict):raise ValueError()
        except (ValueError,TypeError):return self.reply(400,{'error':'bad_request'})
        with LOCK:
            now=time.monotonic()
            while REQUESTS and now-REQUESTS[0]>60:REQUESTS.popleft()
            if len(REQUESTS)>=1200:return self.reply(429,{'error':'rate_limit'})
            REQUESTS.append(now);clean()
            if self.path=='/room/create':
                while CREATES and now-CREATES[0]>60:CREATES.popleft()
                if len(CREATES)>=15:return self.reply(429,{'error':'rate_limit'})
                CREATES.append(now)
                if len(ROOMS)>=MAX_ROOMS:return self.reply(503,{'error':'capacity'})
                room=secrets.token_urlsafe(18);owner=secrets.token_urlsafe(32);guest=secrets.token_urlsafe(32)
                ROOMS[room]={'owner':owner,'guest':guest,'last':time.monotonic(),'state':None,'events':deque(maxlen=100)}
                return self.reply(200,{'room':room,'owner':owner,'guest':guest})
            rid=data.get('room','');token=data.get('token','')
            if not isinstance(rid,str) or not isinstance(token,str):return self.reply(400,{'error':'bad_request'})
            room=ROOMS.get(rid)
            if not room:return self.reply(404,{'error':'room_not_found'})
            if self.path=='/room/host':
                if not secrets.compare_digest(room['owner'],token):return self.reply(403,{'error':'forbidden'})
                state=data.get('state');acks=data.get('acks',[])
                if not isinstance(state,dict) or not isinstance(acks,list) or len(acks)>100 or any(not isinstance(x,str) for x in acks):return self.reply(400,{'error':'bad_request'})
                room['last']=time.monotonic();room['state']=state
                room['events']=deque((e for e in room['events'] if e.get('event_id') not in acks),maxlen=100)
                return self.reply(200,{'events':list(room['events'])})
            if self.path in ('/room/state','/room/event'):
                if not secrets.compare_digest(room['guest'],token):return self.reply(403,{'error':'forbidden'})
                if self.path=='/room/state':return self.reply(200,{'state':room['state']})
                event=data.get('event');fields=('kind','viewer','target','source','event_id')
                if not isinstance(event,dict) or any(not isinstance(event.get(k,''),str) or len(event.get(k,''))>150 for k in fields):return self.reply(400,{'error':'bad_event'})
                event={k:event.get(k,'') for k in fields}
                if event['kind'] not in ('order','trial','court','yes','no') or not event['event_id'] or not event['viewer']:return self.reply(400,{'error':'bad_event'})
                if len(room['events'])>=100:return self.reply(429,{'error':'queue_full'})
                if not any(e['event_id']==event['event_id'] for e in room['events']):room['events'].append(event)
                return self.reply(200,{'ok':True})
            self.reply(404,{'error':'not_found'})

if __name__=='__main__':
    port=int(os.environ.get('PORT','8080'))
    print(f'Room relay listening on {port}',flush=True)
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
