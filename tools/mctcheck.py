"""Independent checker for the Canopy MCT output.

Reads ONLY the .mct text (plus AISC A/Ix/d/b from index.html for section stiffness),
builds a linear 3D frame model with MIDAS conventions, and for every static load case:
  * checks the model is stable (no singular stiffness / mechanism),
  * solves K u = F and recovers support reactions,
  * checks global force and moment equilibrium (reactions + applied = 0),
and validates cross references (element/node/section/load-case names in combos).
Usage: python3 tools/mctcheck.py index.html model.mct [--json] [--forces out.tsv]
  --forces writes a MIDAS-style Beam Forces table (cases, combinations, envelope) that
  can be pasted into the Results tab to exercise it. Section weak-axis inertia and
  torsion constants are approximated (the app's AISC table carries A, Ix, d, b only),
  so use those forces for testing, not design. Requires numpy.
"""
import sys, re, json
import numpy as np

def aisc_table(html):
    tab = {}
    for m in re.finditer(r"'([WH][^']*?)':\[([^\]]+)\]", html):
        name = m.group(1)
        if not (name.startswith('W') or name.startswith('HSS')): continue
        v = [float(x) for x in m.group(2).split(',')]
        d, b, A, w, Ix, Sx = v[:6]
        if name.startswith('W'):
            Iy = max(0.05 * Ix, A * b * b / 24.0)   # approximate weak axis
            J = max(0.01, A ** 3 / 300.0)          # approximate (open section, small)
        else:
            Iy = Ix * (b / d) ** 2
            J = 2.0 * Ix * Iy / (Ix + Iy) * 1.6
        tab[name] = dict(A=A, Iy=Ix, Iz=Iy, J=J, d=d, b=b)  # MIDAS local y = strong axis
    return tab

def parse(text):
    order = []
    cur = None
    for raw in text.split('\n'):
        if raw.startswith('*'):
            key = raw.split(';')[0].strip()
            name = key.split(',')[0].strip()
            arg = key.split(',', 1)[1].strip() if ',' in key else None
            cur = [name, arg, []]
            order.append(cur)
            continue
        s = raw.strip()
        if not s or s.startswith(';') or cur is None: continue
        cur[2].append(s)
    return order

def main():
    html = open(sys.argv[1]).read()
    text = open(sys.argv[2]).read()
    tab = aisc_table(html)
    order = parse(text)
    nodes, elems, secs, cases, combos = {}, {}, {}, [], []
    E = nu = den = None
    supports = {}
    releases = {}
    loads = {}          # case -> dict(sw=vec|None, con=[], beam=[])
    cur_case = None
    errors = []
    for name, arg, rows in order:
        if name == '*NODE':
            for r in rows:
                p = [x.strip() for x in r.split(',')]
                nodes[int(p[0])] = np.array([float(p[1]), float(p[2]), float(p[3])])
        elif name == '*ELEMENT':
            for r in rows:
                p = [x.strip() for x in r.split(',')]
                if p[1] != 'BEAM': errors.append('non-BEAM element ' + p[0])
                elems[int(p[0])] = dict(mat=int(p[2]), sec=int(p[3]), n1=int(p[4]), n2=int(p[5]), beta=float(p[6]))
        elif name == '*MATERIAL':
            p = [x.strip() for x in rows[0].split(',')]
            E = float(p[10]); nu = float(p[11]); den = float(p[13])
        elif name == '*SECTION':
            for r in rows:
                p = [x.strip() for x in r.split(',')]
                desig = p[15]
                if desig not in tab: errors.append('section not in AISC table: ' + desig); continue
                secs[int(p[0])] = tab[desig] | {'name': desig}
        elif name == '*STLDCASE':
            for r in rows: cases.append(r.split(',')[0].strip())
        elif name == '*CONSTRAINT':
            for r in rows:
                p = [x.strip() for x in r.split(',')]
                for nid in p[0].split():
                    supports[int(nid)] = [c == '1' for c in p[1]]
        elif name == '*FRAME-RLS':
            for k in range(0, len(rows), 2):
                a = [x.strip() for x in rows[k].split(',')]
                b = [x.strip() for x in rows[k + 1].split(',')]
                releases[int(a[0])] = ([c == '1' for c in a[2]], [c == '1' for c in b[0]])
        elif name == '*USE-STLD':
            cur_case = arg
            loads.setdefault(cur_case, dict(sw=None, con=[], beam=[]))
        elif name == '*SELFWEIGHT':
            p = [float(x) for x in rows[0].split(',')[:3]]
            loads[cur_case]['sw'] = np.array(p)
        elif name == '*CONLOAD':
            for r in rows:
                p = [x.strip() for x in r.split(',')]
                for nid in p[0].split():
                    loads[cur_case]['con'].append((int(nid), np.array([float(v) for v in p[1:7]])))
        elif name == '*BEAMLOAD':
            for r in rows:
                p = [x.strip() for x in r.split(',')]
                eid, cmd, typ, d, proj = int(p[0]), p[1], p[2], p[3], p[4]
                if typ != 'UNILOAD': errors.append('unsupported beam load ' + typ)
                w1, w2 = float(p[11]), float(p[13])
                if abs(w1 - w2) > 1e-9: errors.append('non-uniform load on %d' % eid)
                loads[cur_case]['beam'].append((eid, d, proj == 'YES', w1))
        elif name == '*LOADCOMB':
            i = 0
            while i < len(rows):
                head = rows[i]; i += 1
                terms = []
                while i < len(rows) and not rows[i].startswith('NAME='):
                    t = [x.strip() for x in rows[i].split(',')]
                    terms += [(t[k], t[k + 1], float(t[k + 2])) for k in range(0, len(t) - 2, 3) if t[k]]
                    i += 1
                combos.append((head.split(',')[0][5:], terms))

    # ---- cross reference checks ----
    for eid, e in elems.items():
        for n in (e['n1'], e['n2']):
            if n not in nodes: errors.append(f'element {eid} references missing node {n}')
        if e['sec'] not in secs: errors.append(f'element {eid} references missing section {e["sec"]}')
    for n in supports:
        if n not in nodes: errors.append(f'support on missing node {n}')
    for c, L in loads.items():
        if c not in cases: errors.append(f'loads for undeclared case {c}')
        for nid, _ in L['con']:
            if nid not in nodes: errors.append(f'{c}: load on missing node {nid}')
        for eid, *_ in L['beam']:
            if eid not in elems: errors.append(f'{c}: load on missing element {eid}')
    cnames = {c for c, _ in combos}
    for c, terms in combos:
        if len(c) > 20: errors.append(f'combo name longer than 20 chars: {c}')
        for kind, lc, fac in terms:
            if kind == 'ST' and lc not in cases: errors.append(f'combo {c} uses unknown case {lc}')
            if kind == 'CBS' and lc not in cnames: errors.append(f'combo {c} uses unknown combo {lc}')
    used = {n for e in elems.values() for n in (e['n1'], e['n2'])}
    free_nodes = [n for n in nodes if n not in used]
    if free_nodes: errors.append(f'{len(free_nodes)} nodes not connected to any element')

    # ---- assemble ----
    ids = sorted(nodes); idx = {n: i for i, n in enumerate(ids)}
    ndof = 6 * len(ids)
    K = np.zeros((ndof, ndof))
    G = E / (2 * (1 + nu))
    T = {}
    def axes(p1, p2, beta):
        ex = (p2 - p1); L = np.linalg.norm(ex); ex /= L
        if abs(abs(ex[2]) - 1) < 1e-9:      # vertical: local z = global X (beta = 0)
            ez = np.array([1.0, 0, 0])
        else:
            Z = np.array([0, 0, 1.0]); ez = Z - ex * (Z @ ex); ez /= np.linalg.norm(ez)
        if abs(beta) > 1e-12: raise SystemExit('beta != 0 not supported')
        ey = np.cross(ez, ex)
        return L, np.vstack([ex, ey, ez])
    def kloc(L, A, Iy, Iz, J):
        k = np.zeros((12, 12))
        a = E * A / L; t = G * J / L
        k[0,0]=k[6,6]=a; k[0,6]=k[6,0]=-a
        k[3,3]=k[9,9]=t; k[3,9]=k[9,3]=-t
        for (v, th, I, s) in ((1, 5, Iz, 1), (2, 4, Iy, -1)):
            c1 = 12*E*I/L**3; c2 = 6*E*I/L**2; c3 = 4*E*I/L; c4 = 2*E*I/L
            k[v,v]=k[v+6,v+6]=c1; k[v,v+6]=k[v+6,v]=-c1
            k[v,th]=k[th,v]=s*c2; k[v,th+6]=k[th+6,v]=s*c2
            k[v+6,th]=k[th,v+6]=-s*c2; k[v+6,th+6]=k[th+6,v+6]=-s*c2
            k[th,th]=k[th+6,th+6]=c3; k[th,th+6]=k[th+6,th]=c4
        return k
    KE = {}
    for eid, e in elems.items():
        p1, p2 = nodes[e['n1']], nodes[e['n2']]
        L, R = axes(p1, p2, e['beta'])
        sp = secs[e['sec']]
        k = kloc(L, sp['A']/144, sp['Iy']/20736, sp['Iz']/20736, sp['J']/20736)
        rel = np.zeros(12, bool)
        if eid in releases:
            ri, rj = releases[eid]; rel[:6] = ri; rel[6:] = rj
        Tm = np.zeros((12, 12))
        for b in range(4): Tm[3*b:3*b+3, 3*b:3*b+3] = R
        KE[eid] = (k, rel, Tm, L, R)
    def condense(k, f, rel):
        r = np.where(rel)[0]; kp = np.where(~rel)[0]
        if len(r) == 0: return k, f
        kc = k.copy(); fc = f.copy()
        krr_inv = np.linalg.inv(k[np.ix_(r, r)])
        kc[np.ix_(kp, kp)] = k[np.ix_(kp, kp)] - k[np.ix_(kp, r)] @ krr_inv @ k[np.ix_(r, kp)]
        fc[kp] = f[kp] - k[np.ix_(kp, r)] @ krr_inv @ f[r]
        kc[r, :] = 0; kc[:, r] = 0; fc[r] = 0
        return kc, fc
    for eid, (k, rel, Tm, L, R) in KE.items():
        kc, _ = condense(k, np.zeros(12), rel)
        kg = Tm.T @ kc @ Tm
        e = elems[eid]; d = np.r_[6*idx[e['n1']]:6*idx[e['n1']]+6, 6*idx[e['n2']]:6*idx[e['n2']]+6]
        K[np.ix_(d, d)] += kg
    fixed = np.zeros(ndof, bool)
    for n, c in supports.items(): fixed[6*idx[n]:6*idx[n]+6] = c
    free = ~fixed
    Kff = K[np.ix_(free, free)]
    sv = np.linalg.svd(Kff, compute_uv=False)
    cond = sv[0] / sv[-1] if sv[-1] > 0 else np.inf
    stable = bool(np.isfinite(cond) and cond < 1e13)

    # ---- per load case ----
    FEF = {}
    def case_vector(c):
        F = np.zeros(ndof); applied = np.zeros(3); mom = np.zeros(3)
        fef = FEF.setdefault(c, {})
        L_ = loads.get(c, dict(sw=None, con=[], beam=[]))
        def add_elem_uniform(eid, qg):  # qg: global force per true length
            k, rel, Tm, L, R = KE[eid]
            ql = R @ qg
            f = np.zeros(12)
            f[0] = f[6] = ql[0]*L/2
            f[1] = f[7] = ql[1]*L/2; f[5] = ql[1]*L*L/12; f[11] = -ql[1]*L*L/12
            f[2] = f[8] = ql[2]*L/2; f[4] = -ql[2]*L*L/12; f[10] = ql[2]*L*L/12
            _, fc = condense(k, f, rel)
            fef[eid] = fef.get(eid, np.zeros(12)) + fc
            fg = Tm.T @ fc
            e = elems[eid]
            F[6*idx[e['n1']]:6*idx[e['n1']]+6] += fg[:6]; F[6*idx[e['n2']]:6*idx[e['n2']]+6] += fg[6:]
            tot = qg * L
            mid = (nodes[e['n1']] + nodes[e['n2']]) / 2
            return tot, np.cross(mid, tot)
        if L_['sw'] is not None:
            for eid in elems:
                qg = L_['sw'] * den * secs[elems[eid]['sec']]['A'] / 144
                t, m = add_elem_uniform(eid, qg); applied += t; mom += m
        for nid, v in L_['con']:
            F[6*idx[nid]:6*idx[nid]+6] += v
            applied += v[:3]; mom += np.cross(nodes[nid], v[:3]) + v[3:]
        for eid, d, proj, w in L_['beam']:
            k, rel, Tm, L, R = KE[eid]
            if d in ('GX', 'GY', 'GZ'):
                u = np.eye(3)['XYZ'.index(d[1])]
                scale = np.sqrt(max(0.0, 1 - (R[0] @ u) ** 2)) if proj else 1.0
                qg = u * w * scale
            else:
                if proj: errors.append('projected local load not supported')
                qg = R['XYZ'.index(d[1])] * w
            t, m = add_elem_uniform(eid, qg); applied += t; mom += m
        return F, applied, mom
    out = dict(nodes=len(nodes), elements=len(elems), supports=len(supports), releases=len(releases),
               cases=cases, combos=len(combos), stable=stable, cond=float(cond), errors=errors, results={})
    U = {}
    for c in cases:
        F, applied, mom = case_vector(c)
        u = np.zeros(ndof)
        if stable: u[free] = np.linalg.solve(Kff, F[free])
        U[c] = u
        Rv = K @ u - F
        rf = np.zeros(3); rm = np.zeros(3)
        for n in supports:
            r = Rv[6*idx[n]:6*idx[n]+6]
            rf += r[:3]; rm += np.cross(nodes[n], r[:3]) + r[3:]
        resid_free = float(np.abs(Rv[free]).max()) if free.any() else 0.0
        scaleF = max(1.0, float(np.abs(applied).max()))
        out['results'][c] = dict(applied=applied.round(3).tolist(), reactions=rf.round(3).tolist(),
                                 force_balance=float(np.abs(applied + rf).max()),
                                 moment_balance=float(np.abs(mom + rm).max()),
                                 free_dof_residual=resid_free,
                                 max_disp_in=float(np.abs(u.reshape(-1, 6)[:, :3]).max() * 12))
    if '--forces' in sys.argv:
        path = sys.argv[sys.argv.index('--forces') + 1]
        def member_forces(c):
            res = {}
            for eid, (k, rel, Tm, L, R) in KE.items():
                e = elems[eid]
                ug = np.r_[U[c][6*idx[e['n1']]:6*idx[e['n1']]+6], U[c][6*idx[e['n2']]:6*idx[e['n2']]+6]]
                kc, _ = condense(k, np.zeros(12), rel)
                fl = kc @ (Tm @ ug) - FEF.get(c, {}).get(eid, np.zeros(12))
                # MIDAS-style: I end reports -f_i, J end reports f_j (internal force sign)
                res[eid] = (-fl[:6], fl[6:])
            return res
        caseF = {c: member_forces(c) for c in cases}
        lines = ['\tElem\tLoad\tPart\tAxial (lbf)\tShear-y (lbf)\tShear-z (lbf)\tTorsion (lbf*ft)\tMoment-y (lbf*ft)\tMoment-z (lbf*ft)']
        def emit(name, res):
            for eid in sorted(res):
                e = elems[eid]
                for part, n, v in (('I', e['n1'], res[eid][0]), ('J', e['n2'], res[eid][1])):
                    lines.append('\t%d\t%s\t%s[%d]\t%s' % (eid, name, part, n, '\t'.join('%.2f' % x for x in v)))
        for c in cases: emit(c, caseF[c])
        env = {}
        for name, terms in combos:
            if name == 'Envelope': continue
            res = {}
            for eid in elems:
                a = np.zeros(6); b = np.zeros(6)
                for kind, lc, fac in terms:
                    a = a + fac * caseF[lc][eid][0]; b = b + fac * caseF[lc][eid][1]
                res[eid] = (a, b)
                if eid not in env: env[eid] = (a.copy(), b.copy())
                else:
                    ea, eb = env[eid]
                    env[eid] = (np.where(np.abs(a) > np.abs(ea), a, ea), np.where(np.abs(b) > np.abs(eb), b, eb))
            emit(name, res)
        emit('Envelope', env)
        open(path, 'w').write('\n'.join(lines))
    if '--json' in sys.argv: print(json.dumps(out)); return
    print(f"nodes {out['nodes']}  elements {out['elements']}  supports {out['supports']}  releases {out['releases']}  combos {out['combos']}")
    print(f"stable: {stable}   condition number {cond:.3g}")
    print('cross-reference errors:', errors or 'none')
    for c, r in out['results'].items():
        print(f"  {c:12s} applied {r['applied']}  reactions {r['reactions']}  |force imbalance| {r['force_balance']:.2e}  |moment imbalance| {r['moment_balance']:.2e}  max disp {r['max_disp_in']:.3f} in")

main()
