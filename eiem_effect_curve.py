"""Native AnimationCurve math and edits; no Blender data or projection assets."""
import copy
import math


def bezier(a, b, c, d, t):
    s=1-t
    return s*s*s*a+3*s*s*t*b+3*s*t*t*c+t*t*t*d


def controls(a,b):
    dt=b['time']-a['time']
    ow=a.get('outWeight',1/3) if int(a.get('weightedMode',0))&2 else 1/3
    iw=b.get('inWeight',1/3) if int(b.get('weightedMode',0))&1 else 1/3
    return (a['time'],a['time']+dt*ow,b['time']-dt*iw,b['time']), (
        a['value'],a['value']+dt*ow*a.get('outSlope',0),
        b['value']-dt*iw*b.get('inSlope',0),b['value'])


def parameter(xs,t):
    lo,hi=0.0,1.0
    u=max(0,min(1,(t-xs[0])/max(1e-30,xs[-1]-xs[0])))
    for _ in range(28):
        error=bezier(*xs,u)-t
        if abs(error)<1e-11:return u
        if error<0:lo=u
        else:hi=u
        derivative=3*((1-u)**2*(xs[1]-xs[0])+2*(1-u)*u*(xs[2]-xs[1])+u*u*(xs[3]-xs[2]))
        candidate=u-error/derivative if derivative>1e-15 else -1
        u=candidate if lo<candidate<hi else (lo+hi)/2
    return u


def evaluate(keys,t):
    if not keys:return 1.0
    if t<=keys[0]['time']:return keys[0]['value']
    if t>=keys[-1]['time']:return keys[-1]['value']
    for a,b in zip(keys,keys[1:]):
        if not a['time']<=t<=b['time']:continue
        dt=b['time']-a['time']
        if dt<=0:return b['value']
        if int(a.get('weightedMode',0))&2 or int(b.get('weightedMode',0))&1:
            xs,ys=controls(a,b)
            return bezier(*ys,parameter(xs,t))
        u=(t-a['time'])/dt
        return ((2*u**3-3*u*u+1)*a['value']+(u**3-2*u*u+u)*dt*a.get('outSlope',0)
                +(-2*u**3+3*u*u)*b['value']+(u**3-u*u)*dt*b.get('inSlope',0))
    return keys[-1]['value']


def integral(keys,end):
    """Integrate y dx exactly in Bezier parameter space, including weights."""
    if end<=0:return 0.0
    if not keys:return end
    total=keys[0]['value']*min(end,max(0,keys[0]['time']))
    for a,b in zip(keys,keys[1:]):
        lo,hi=max(0,a['time']),min(end,b['time'])
        if hi<=lo:continue
        dt=b['time']-a['time']
        weighted=int(a.get('weightedMode',0))&2 or int(b.get('weightedMode',0))&1
        if not weighted:
            v0,v1=a['value'],b['value'];m0=a.get('outSlope',0)*dt;m1=b.get('inSlope',0)*dt
            c3=2*v0-2*v1+m0+m1;c2=-3*v0+3*v1-2*m0-m1
            def area(u):return dt*(c3*u**4/4+c2*u**3/3+m0*u*u/2+v0*u)
            total+=area((hi-a['time'])/dt)-area((lo-a['time'])/dt)
            if b['time']>=end:break
            continue
        xs,ys=controls(a,b)
        def polynomial(v):return (v[0],3*(v[1]-v[0]),3*(v[2]-2*v[1]+v[0]),v[3]-3*v[2]+3*v[1]-v[0])
        xp,yp=polynomial(xs),polynomial(ys)
        product=[0.0]*6
        for i,y in enumerate(yp):
            for j in range(1,4):product[i+j-1]+=y*j*xp[j]
        u0=parameter(xs,lo);u1=parameter(xs,hi)
        total+=sum(v*(u1**(i+1)-u0**(i+1))/(i+1) for i,v in enumerate(product))
        if b['time']>=end:break
    return total+max(0,end-max(0,keys[-1]['time']))*keys[-1]['value']


def new_key(time,value,template=None):
    key=copy.deepcopy(template) if template else dict(time=0.0,value=0.0,inSlope=0.0,outSlope=0.0,
        weightedMode=0,inWeight=1/3,outWeight=1/3)
    key.update(time=float(time),value=float(value),inSlope=0.0,outSlope=0.0,weightedMode=0,inWeight=1/3,outWeight=1/3)
    return key


def handle(keys,index,side):
    key=keys[index]
    interval=(key['time']-keys[index-1]['time'] if index else keys[1]['time']-key['time'] if len(keys)>1 else 1) if side=='in' else (
        keys[index+1]['time']-key['time'] if index+1<len(keys) else key['time']-keys[index-1]['time'] if index else 1)
    bit=1 if side=='in' else 2
    weight=key.get(side+'Weight',1/3) if int(key.get('weightedMode',0))&bit else 1/3
    dx=max(1e-9,interval)*weight*(-1 if side=='in' else 1)
    return key['time']+dx,key['value']+key.get(side+'Slope',0)*dx


class Model:
    """A cancellable editing buffer. Untouched native fields stay byte-stable."""
    def __init__(self,channels):
        self.channels=copy.deepcopy(channels)
        self.original=copy.deepcopy(channels)
        self.history=[];self.future=[]
        self.channel=0;self.selected=None

    @property
    def keys(self):return self.channels[self.channel]['data']['m_Curve']
    @property
    def factor(self):return self.channels[self.channel]['factor']

    def checkpoint(self):
        self.history.append(copy.deepcopy((self.channels,self.channel,self.selected)))
        self.history=self.history[-64:];self.future.clear()

    def undo(self,redo=False):
        source,target=(self.future,self.history) if redo else (self.history,self.future)
        if not source:return False
        target.append(copy.deepcopy((self.channels,self.channel,self.selected)))
        self.channels,self.channel,self.selected=source.pop()
        return True

    def move(self,index,time,value):
        keys=self.keys
        lo=keys[index-1]['time']+1e-6 if index else 0.0
        hi=keys[index+1]['time']-1e-6 if index+1<len(keys) else 1.0
        if lo>hi:raise ValueError('关键点过近，请先用精确数值调整时间')
        if not all(math.isfinite(v) for v in (time,value)):raise ValueError('时间和数值必须是有限数字')
        keys[index]['time']=max(lo,min(hi,float(time)))
        keys[index]['value']=float(value)/self.factor

    def move_handle(self,index,side,time,value):
        if not all(math.isfinite(v) for v in (time,value)):raise ValueError('切线需要有限数字')
        key=self.keys[index]
        neighbor=(self.keys[index-1]['time'] if index else key['time']-1) if side=='in' else (
            self.keys[index+1]['time'] if index+1<len(self.keys) else key['time']+1)
        interval=abs(key['time']-neighbor)
        time=max(neighbor,min(key['time']-1e-7,time)) if side=='in' else min(neighbor,max(key['time']+1e-7,time))
        dx=time-key['time']
        key[side+'Slope']=(value/self.factor-key['value'])/dx
        key[side+'Weight']=min(1.0,max(0.0,abs(dx)/max(1e-9,interval)))
        key['weightedMode']=int(key.get('weightedMode',0))|(1 if side=='in' else 2)

    def add(self,time,value):
        if not all(math.isfinite(v) for v in (time,value)):raise ValueError('时间和数值必须是有限数字')
        time=max(0.0,min(1.0,time))
        if any(abs(key['time']-time)<1e-6 for key in self.keys):raise ValueError('该时间已有关键点')
        if len(self.keys)>=4096:raise ValueError('曲线关键点过多')
        self.keys.append(new_key(time,value/self.factor,self.keys[0] if self.keys else None))
        self.keys.sort(key=lambda k:k['time'])
        self.selected=next(i for i,k in enumerate(self.keys) if k['time']==time)

    def delete(self):
        if self.selected is None:return
        del self.keys[self.selected]
        self.selected=None

    def tangent(self,kind):
        keys=self.keys
        indices=range(len(keys)) if self.selected is None else [self.selected]
        for i in indices:
            key=keys[i]
            left=(key['value']-keys[i-1]['value'])/(key['time']-keys[i-1]['time']) if i else 0
            right=(keys[i+1]['value']-key['value'])/(keys[i+1]['time']-key['time']) if i+1<len(keys) else 0
            smooth=(keys[min(i+1,len(keys)-1)]['value']-keys[max(0,i-1)]['value'])/max(1e-9,keys[min(i+1,len(keys)-1)]['time']-keys[max(0,i-1)]['time'])
            key.update(inSlope=left if kind=='LINEAR' else smooth if kind=='SMOOTH' else 0.0,
                       outSlope=right if kind=='LINEAR' else smooth if kind=='SMOOTH' else 0.0)

    def preset(self,kind):
        values={'ONE':[(0,1),(1,1)],'UP':[(0,0),(1,1)],'DOWN':[(0,1),(1,0)],'PULSE':[(0,0),(.5,1),(1,0)]}[kind]
        template=self.keys[0] if self.keys else None
        self.channels[self.channel]['data']['m_Curve']=[new_key(t,v,template) for t,v in values]
        self.selected=None
        self.tangent('LINEAR' if kind in ('UP','DOWN') else 'FLAT')

    def bounds(self):
        values=[evaluate(c['data']['m_Curve'],i/128)*c['factor'] for c in self.channels for i in range(129)]
        values += [k['value']*c['factor'] for c in self.channels for k in c['data']['m_Curve']]
        lo,hi=min(values+[0.0]),max(values+[0.0])
        span=max(hi-lo,abs(hi)*.2,1e-3)
        return [-.03,1.03,lo-span*.12,hi+span*.12]
