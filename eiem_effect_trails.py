"""Bounded, seekable approximation of Unity TrailModule paths.

Particle trajectories come from the existing deterministic simulator. Trails
are reconstructed from time samples, never unbounded playback history. This
does not execute game/custom ribbon scripts or sub-emitter topology.
"""
import math

if __package__:from . import eiem_effect_simulation as sim
else:import eiem_effect_simulation as sim


def paths(data, time, seed=1, samples=40):
    module = data.get('TrailModule', {})
    if not module.get('enabled') or time < 0:
        return [], []
    mode = module.get('mode', 0)
    live = sim.particles(data, time, seed)
    ratio = max(0., min(1., module.get('ratio', 1)))
    selected = {p['id']: p for p in live if p['random'] <= ratio}
    if mode == 1:
        count = max(1, min(64, module.get('ribbonCount', 1)))
        groups = [[] for _ in range(count)]
        for index, particle in enumerate(sorted(selected.values(), key=lambda p: (p['born'], p['id']))):
            groups[index % count].append((time, particle))
        warnings = ['Ribbon：按出生次序连接当前粒子，非游戏脚本执行结果']
        if module.get('splitSubEmitterRibbons'):
            return [], ['Ribbon 子发射器分组未解析，停止连接以免串带']
        return [g for g in groups if len(g) > 1], warnings
    if mode != 0:
        return [], ['未知 TrailModule mode：' + str(mode)]
    speed = data.get('simulationSpeed', 1)
    if speed <= 0:
        return [], []
    # Bound the history by the maximum sampled native lifetime curve. This is
    # explicitly a sampled preview, not Unity's per-update vertex insertion.
    lifetime = max(sim.scalar(module.get('lifetime', 1), t=i / 16, r=r)
                   for i in range(17) for r in (0, 1))
    if module.get('sizeAffectsLifetime'):
        size = data.get('InitialModule', {}).get('startSize', 1)
        lifetime *= max(sim.scalar(size, t=i / 16, r=r, fallback=1) for i in range(17) for r in (0, 1))
    window = max(0, lifetime / speed)
    if window <= 0:
        return [], []
    begin = max(0., time - window)
    steps = max(2, min(96, int(samples)))
    points = {}
    budget = 0
    for index in range(steps + 1):
        at = begin + (time - begin) * index / steps
        sampled = live if index == steps else sim.particles(data, at, seed)
        budget += len(sampled)
        if budget > 100000:
            return [], ['Trail 轨迹采样超出 100000 点预算；原生模块未修改']
        for particle in sampled:
            if particle['random'] > ratio or module.get('dieWithParticles', True) and particle['id'] not in selected:
                continue
            duration = sim.scalar(module.get('lifetime', 1), particle['phase'], particle['random'])
            if module.get('sizeAffectsLifetime'):
                duration *= particle.get('initial_size', particle['size'])
            if (time - at) * speed <= duration + 1e-6:
                points.setdefault(particle['id'], []).append((at, particle))
    warnings = ['Trail：有限时间采样重建（非 Unity 逐帧顶点缓存）']
    if data.get('prewarm'):
        warnings.append('预热前的 Trail 历史暂未重建')
    return [p for p in points.values() if len(p) > 1], warnings


def filtered(points, minimum):
    """Keep the live endpoint, while respecting source minimum vertex distance."""
    if len(points) < 2:
        return points
    result = [points[0]]
    for point in points[1:-1]:
        if math.dist(result[-1][0], point[0]) >= minimum:
            result.append(point)
    if math.dist(result[-1][0], points[-1][0]) > 1e-8:
        result.append(points[-1])
    return result
