"""Deterministic, bpy-free preview of ordinary Unity particle modules.

Native source fields remain authoritative. Unsupported modules are reported by
the Blender adapter; this sampler never rewrites author data to fit its model.
"""
import math
import random
import json
import heapq
from functools import lru_cache
if __package__:from .eiem_effect_curve import evaluate as curve, integral as curve_integral
else:from eiem_effect_curve import evaluate as curve, integral as curve_integral


def scalar(value, t=0, r=.5, fallback=0):
    if not isinstance(value, dict):
        return float(value) if isinstance(value, (int, float)) else fallback
    mode = value.get('minMaxState', 0)
    high, low = value.get('scalar', fallback), value.get('minScalar', fallback)
    if mode == 0:
        return high
    if mode == 1:
        return high*curve(value.get('maxCurve', {}).get('m_Curve', []), t)
    if mode == 2:
        a = curve(value.get('maxCurve', {}).get('m_Curve', []), t)
        b = curve(value.get('minCurve', {}).get('m_Curve', []), t)
        return high*(b+(a-b)*r)
    if mode == 3:
        return low+(high-low)*r
    return fallback


def _rgba(color):
    if isinstance(color, int):
        return tuple(((color >> (8*i)) & 255)/255 for i in range(4))
    return tuple(color.get(k, 1.0) for k in ('r', 'g', 'b', 'a'))


def gradient(value, t):
    if not isinstance(value, dict):
        return (1, 1, 1, 1)
    # Serialized Gradient uses separate color/alpha key times in uint16 units.
    colors = [(value.get('ctime'+str(i), 0)/65535, _rgba(value.get('key'+str(i), {})))
              for i in range(value.get('m_NumColorKeys', 0))]
    alphas = [(value.get('atime'+str(i), 0)/65535, _rgba(value.get('key'+str(i), {}))[3])
              for i in range(value.get('m_NumAlphaKeys', 0))]
    def sample(keys, default):
        if not keys:
            return default
        keys.sort(key=lambda p:p[0])
        if t <= keys[0][0]:
            return keys[0][1]
        for a, b in zip(keys, keys[1:]):
            if t <= b[0]:
                u=(t-a[0])/max(1e-12,b[0]-a[0])
                if value.get('m_Mode') == 1:
                    u=0
                if isinstance(a[1], tuple):
                    return tuple(x+(y-x)*u for x,y in zip(a[1],b[1]))
                return a[1]+(b[1]-a[1])*u
        return keys[-1][1]
    col=sample(colors,(1,1,1,1))
    return (*col[:3], sample(alphas,1))


def color(value, t=0, r=.5):
    mode=value.get('minMaxState',0)
    if mode == 0:
        return _rgba(value.get('maxColor',{}))
    if mode == 1:
        return gradient(value.get('maxGradient',{}),t)
    if mode == 3:
        a,b=gradient(value.get('minGradient',{}),t),gradient(value.get('maxGradient',{}),t)
    elif mode == 2:
        a,b=_rgba(value.get('minColor',{})),_rgba(value.get('maxColor',{}))
    elif mode == 4:
        return gradient(value.get('maxGradient',{}),r)
    else:
        return (1,1,1,1)
    return tuple(x+(y-x)*r for x,y in zip(a,b))


SHAPES={0:'Sphere',1:'SphereShell',2:'Hemisphere',3:'HemisphereShell',4:'Cone',5:'Box',6:'Mesh',7:'ConeShell',8:'ConeVolume',9:'ConeVolumeShell',10:'Circle',11:'CircleEdge',12:'SingleSidedEdge',13:'MeshRenderer',14:'SkinnedMeshRenderer',15:'BoxShell',16:'BoxEdge',17:'Donut',18:'Rectangle',19:'Sprite',20:'SpriteRenderer'}

def rotate(vector,degrees):
    x,y,z=vector
    # Unity Euler application order: Z, X, Y.
    a,b,c=(math.radians(degrees.get(k,0)) for k in ('x','y','z'))
    x,y=x*math.cos(c)-y*math.sin(c),x*math.sin(c)+y*math.cos(c)
    y,z=y*math.cos(a)-z*math.sin(a),y*math.sin(a)+z*math.cos(a)
    return (x*math.cos(b)+z*math.sin(b),y,-x*math.sin(b)+z*math.cos(b))

def shape_transform(shape,point,direction=False):
    scaled=tuple(v*(1 if direction else shape.get('m_Scale',{}).get(k,1)) for v,k in zip(point,('x','y','z')))
    rotated=rotate(scaled,shape.get('m_Rotation',{}))
    return tuple(v+(0 if direction else shape.get('m_Position',{}).get(k,0)) for v,k in zip(rotated,('x','y','z')))

def shape_point(shape, rng):
    """Point and initial direction in source emitter coordinates."""
    if not shape.get('enabled',False):
        return (0,0,0),(0,0,1)
    kind=shape.get('type',0)
    arc=shape.get('arc',{}).get('value',360) if kind in (4,7,8,9,10,11,17) else 360
    az=rng.random()*math.radians(arc)
    radius=shape.get('radius',{}).get('value',1)
    thickness=shape.get('radiusThickness',1)
    rr=radius*math.sqrt((1-thickness)**2+(1-(1-thickness)**2)*rng.random())
    direction=(0,0,1)
    if kind in (5,15,16):  # box, box shell, box edge
        p=tuple(rng.uniform(-.5,.5) for _ in range(3))
        if kind in (15,16):
            p=list(p);p[rng.randrange(3)]=rng.choice((-.5,.5))
            if kind==16:
                axis=rng.choice([i for i in range(3) if abs(p[i])!=.5]);p[axis]=rng.choice((-.5,.5))
    elif kind in (0,1,2,3):  # sphere/shell, hemisphere/shell
        z=rng.uniform(-1,1) if kind<2 else rng.random()
        q=math.sqrt(max(0,1-z*z));direction=(q*math.cos(az),q*math.sin(az),z)
        rad=radius if kind in (1,3) else radius*((1-thickness)**3+(1-(1-thickness)**3)*rng.random())**(1/3)
        p=tuple(rad*x for x in direction)
    elif kind in (4,7,8,9):  # cone base/shell/volume/volume shell
        angle=math.radians(shape.get('angle',25))
        z=rng.random()*shape.get('length',1) if kind in (8,9) else 0
        if kind in (7,9):rr=radius
        rr+=z*math.tan(angle)
        p=(rr*math.cos(az),rr*math.sin(az),z)
        direction=(math.sin(angle)*math.cos(az),math.sin(angle)*math.sin(az),math.cos(angle))
    elif kind in (10,11):
        if kind==11:rr=radius
        p=(rr*math.cos(az),rr*math.sin(az),0)
        direction=(math.cos(az),math.sin(az),0)
    elif kind==12:
        p=(rng.uniform(-radius,radius),0,0)
    elif kind==18:
        p=(rng.uniform(-.5,.5),rng.uniform(-.5,.5),0)
    elif kind==17:
        minor=shape.get('donutRadius',.2);angle=rng.random()*math.tau
        tube=minor*math.sqrt(rng.random());p=((radius+tube*math.cos(angle))*math.cos(az),(radius+tube*math.cos(angle))*math.sin(az),tube*math.sin(angle))
        direction=(math.cos(angle)*math.cos(az),math.cos(angle)*math.sin(az),math.sin(angle))
    else:
        raise ValueError('暂未还原发射形状 '+SHAPES.get(kind,str(kind)))
    random_amount=shape.get('randomDirectionAmount',0)
    if random_amount:
        random_dir=[rng.uniform(-1,1) for _ in range(3)];n=math.sqrt(sum(v*v for v in random_dir)) or 1
        direction=tuple(a*(1-random_amount)+b/n*random_amount for a,b in zip(direction,random_dir))
        n=math.sqrt(sum(v*v for v in direction)) or 1;direction=tuple(v/n for v in direction)
    return shape_transform(shape,p),shape_transform(shape,direction,True)

def integrate(value,end,r=.5):
    if end<=0:return 0
    if not isinstance(value,dict) or value.get('minMaxState',0) in (0,3):return scalar(value,r=r)*end
    high=curve_integral(value.get('maxCurve',{}).get('m_Curve',[]),end)
    if value.get('minMaxState')==2:
        low=curve_integral(value.get('minCurve',{}).get('m_Curve',[]),end);high=low+(high-low)*r
    return value.get('scalar',0)*high


def integrate_product(value, modifier, end, r=.5, modifier_r=.5):
    """Integrate velocity * speed modifier, splitting at both curves' keys.

    Eight-point Gaussian quadrature integrates unweighted cubic products exactly;
    weighted Beziers use the same bounded sampler without per-frame substeps.
    """
    if not isinstance(modifier,dict) or modifier.get('minMaxState',0) in (0,3):
        return integrate(value,end,r)*scalar(modifier,r=modifier_r,fallback=1)
    if end<=0:return 0.0
    points={0.0,end}
    for item in (value,modifier):
        if isinstance(item,dict) and item.get('minMaxState') in (1,2):
            for name in ('minCurve','maxCurve'):
                points.update(k['time'] for k in item.get(name,{}).get('m_Curve',[]) if 0<k['time']<end)
    points=sorted(points);total=0.0
    quadrature=((.1834346424956498,.3626837833783620),(.5255324099163290,.3137066458778873),
                (.7966664774136267,.2223810344533745),(.9602898564975363,.1012285362903763))
    for a,b in zip(points,points[1:]):
        mid=(a+b)*.5;half=(b-a)*.5
        for x,w in quadrature:
            for t in (mid-half*x,mid+half*x):
                total+=half*w*scalar(value,t,r)*scalar(modifier,t,modifier_r,fallback=1)
    return total

@lru_cache(maxsize=32)
def _rate_data(serialized):
    return json.loads(serialized)

@lru_cache(maxsize=32768)
def emission_birth(serialized,duration,looping,ordinal):
    """An ordinal has one birth time, independent of seek and other modules.

    Cache only immutable emission inputs. Speed/size/color edits reuse births;
    rate edits use a different key. Bounded caches retain no Blender objects.
    """
    rate=_rate_data(serialized);total=max(0,duration*integrate(rate,1))
    if total<=0:return float('inf')
    cycle=max(0,math.ceil(ordinal/total)-1) if looping else 0
    target=ordinal-cycle*total;lo,hi=0.0,duration
    for _ in range(36):
        mid=(lo+hi)*.5
        if duration*integrate(rate,mid/duration)<target:lo=mid
        else:hi=mid
    return cycle*duration+(lo+hi)*.5

def uv_transform(module,particle,uv):
    if not module.get('enabled'):return uv
    if module.get('mode',0)!=0:raise ValueError('Sprite 列表贴图动画暂不支持')
    nx=max(1,module.get('tilesX',1));ny=max(1,module.get('tilesY',1));n=nx*ny
    single_row=module.get('animationType',0)==1
    frame_count=nx if single_row else n
    phase=particle['phase']*module.get('cycles',1)
    frame=scalar(module.get('startFrame',{}),r=particle['random'])*frame_count
    if module.get('timeMode',0)==2:frame+=particle['age']*module.get('fps',30)
    else:frame+=scalar(module.get('frameOverTime',{}),phase%1,particle['random'])*frame_count
    frame=int(frame)%frame_count
    if single_row:frame=(int(particle['random']*ny) if module.get('rowMode',0)==1 else module.get('rowIndex',0))*nx+frame
    return ((uv[0]+frame%nx)/nx,(uv[1]+ny-1-frame//nx)/ny)


def particles(data, time, seed=1):
    if time < 0:
        return []
    speed=data.get('simulationSpeed',1)
    time=time*speed-scalar(data.get('startDelay',{}))
    if time < 0:
        return []
    initial=data.get('InitialModule',{})
    emission=data.get('EmissionModule',{})
    if not emission.get('enabled',False):
        return []
    limit=max(0,int(initial.get('maxNumParticles',1000)))
    if limit==0:return []
    if limit>20000:raise ValueError('离线预览上限为 20000 粒子；原生数据未修改')
    duration=max(1e-6,data.get('lengthInSec',5))
    prewarm=duration if data.get('prewarm') and data.get('looping') else 0
    time+=prewarm
    # Capacity rejects new births; it never replaces still-living particles.
    # Replaying from origin also makes arbitrary timeline seeks deterministic.
    begin=0
    births=[]
    # Count from system origin, never reset the accumulator at the lifetime window.
    rate=emission.get('rateOverTime',{});cycle_total=max(0,duration*integrate(rate,1))
    def cumulative(t):
        t=max(0,t)
        if not data.get('looping',False):return max(0,duration*integrate(rate,min(1,t/duration)))
        cycles=int(t/duration);return cycles*cycle_total+max(0,duration*integrate(rate,(t%duration)/duration))
    first=int(cumulative(begin))+1;last=int(cumulative(time)+1e-9)
    if last-first>50000:raise ValueError('离线发射量超出预览预算；原生数据未修改')
    serialized=json.dumps(rate,sort_keys=True,separators=(',',':'))
    for ordinal in range(first,last+1):
        if not isinstance(rate,dict) or rate.get('minMaxState',0)==0:
            births.append((ordinal/max(1e-12,scalar(rate)),ordinal));continue
        births.append((emission_birth(serialized,duration,bool(data.get('looping')),ordinal),ordinal))
    cycles=range(max(0,int(begin/duration)-1),int(time/duration)+1) if data.get('looping',False) else (0,)
    for cycle in cycles:
        for bi,burst in enumerate(emission.get('m_Bursts',[])):
            rng=random.Random(seed+cycle*3571+bi)
            if rng.random()>burst.get('probability',1):
                continue
            for repeat in range(max(1,burst.get('cycleCount',1))):
                born=cycle*duration+burst.get('time',0)+repeat*burst.get('repeatInterval',0)
                if begin<=born<=time:
                    count=max(0,int(scalar(burst.get('countCurve',{}),r=rng.random())))
                    if count>50000 or len(births)+count>50000:raise ValueError('离线 burst 数量超出预览预算；原生数据未修改')
                    births.extend((born,-(1+cycle*1000000000000+bi*10000000+repeat*100000+n)) for n in range(count))
    result=[];deaths=[]
    for born,identity in sorted(births):
        rng=random.Random(seed*7919+identity)
        rr=rng.random();emitter_phase=(born%duration)/duration;life=scalar(initial.get('startLifetime',{}),emitter_phase,rr)
        while deaths and deaths[0]<=born+1e-9:heapq.heappop(deaths)
        if life<=0 or len(deaths)>=limit:continue
        heapq.heappush(deaths,born+life)
        age=time-born
        if life<=0 or not 0<=age<life:
            continue
        phase=age/life
        pos,direction=shape_point(data.get('ShapeModule',{}),rng)
        start=scalar(initial.get('startSpeed',{}),emitter_phase,rng.random())
        velocity=data.get('VelocityModule',{})
        vr=[rng.random() for _ in range(3)] if velocity.get('enabled') else [0]*3
        modifier=velocity.get('speedModifier',1) if velocity.get('enabled') else 1
        travel=life*integrate_product(start,modifier,phase,modifier_r=rr)
        v=tuple(life*integrate_product(velocity.get(k,0),modifier,phase,r,rr) if velocity.get('enabled') else 0 for k,r in zip(('x','y','z'),vr))
        gravity=scalar(initial.get('gravityModifier',{}),phase,rng.random())
        local_position=[pos[i]+direction[i]*travel+(0 if velocity.get('inWorldSpace') else v[i]) for i in range(3)]
        world_offset=[v[i] if velocity.get('inWorldSpace') else 0 for i in range(3)]
        world_offset[1]-=.5*9.81*gravity*age*age
        noise=data.get('NoiseModule',{})
        if noise.get('enabled'):
            frequency=noise.get('frequency',.5)
            amplitude=scalar(noise.get('strength',{}),phase,rng.random())
            for i in range(3):
                local_position[i]+=math.sin(age*frequency*math.tau+rr*50+i*2)*amplitude*.15
        position=[a+b for a,b in zip(local_position,world_offset)]
        size=scalar(initial.get('startSize',{}),emitter_phase,rr,fallback=1)
        sizes=[size,scalar(initial.get('startSizeY',{}),emitter_phase,rr,fallback=1),scalar(initial.get('startSizeZ',{}),emitter_phase,rr,fallback=1)] if initial.get('size3D') else [size]*3
        sm=data.get('SizeModule',{})
        if sm.get('enabled'):
            sizes=[s*scalar(sm.get(k,sm.get('curve',{})),phase,rr,fallback=1) for s,k in zip(sizes,('curve','y','z'))] if sm.get('separateAxes') else [s*scalar(sm.get('curve',{}),phase,rr,fallback=1) for s in sizes]
        size=sizes[0]
        tint=color(initial.get('startColor',{}),r=rr)
        cm=data.get('ColorModule',{})
        if cm.get('enabled'):
            tint=tuple(a*b for a,b in zip(tint,color(cm.get('gradient',{}),phase,rr)))
        rotation=scalar(initial.get('startRotation',{}),emitter_phase,rr)
        rotations=[scalar(initial.get('startRotationX',{}),emitter_phase,rr),scalar(initial.get('startRotationY',{}),emitter_phase,rr),rotation] if initial.get('rotation3D') else [0,0,rotation]
        rm=data.get('RotationModule',{})
        if rm.get('enabled'):
            if rm.get('separateAxes'):rotations=[a+life*integrate(rm.get(k,{}),phase,rr) for a,k in zip(rotations,('x','y','curve'))]
            else:rotations[2]+=life*integrate(rm.get('curve',{}),phase,rr)
        result.append(dict(id=identity,born=born-prewarm,age=age,phase=phase,position=position,local_position=local_position,world_offset=world_offset,
            origin=pos,size=size,sizes=sizes,color=tint,rotation=rotations[2],rotations=rotations,random=rr))
    return sorted(result,key=lambda p:p['born'])
