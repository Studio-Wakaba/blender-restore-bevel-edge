bl_info = {
    "name": "Restore Bevel Edge",
    "author": "STUDIO WAKABA",
    "version": (1, 2, 1),
    "blender": (5, 2, 0),
    "location": "Edit Mode > Right Click > ベベルを元のエッジに戻す",
    "description": "Restore a simple applied bevel back to the original sharp edge",
    "category": "Mesh",
}

import bpy
import bmesh
from mathutils.geometry import intersect_plane_plane

EPS = 1e-10


def plane_line(f1, f2):
    r = intersect_plane_plane(
        f1.calc_center_median(), f1.normal,
        f2.calc_center_median(), f2.normal
    )
    if not r:
        return None
    p, d = r
    if d.length_squared < EPS:
        return None
    d.normalize()
    return p, d


def project_line(co, line):
    p, d = line
    return p + d * (co - p).dot(d)


def refresh(context, bm, me, restored_edges=None):
    """Cleanup, recalc normals, update edit mesh, and keep result selected."""
    restored_edges = restored_edges or []

    # Remove zero-length edges left by collapsing the bevel.
    zero_edges = [
        e for e in bm.edges
        if e.is_valid and (e.verts[0].co - e.verts[1].co).length_squared < EPS
    ]
    if zero_edges:
        bmesh.ops.dissolve_edges(
            bm,
            edges=zero_edges,
            use_verts=True,
            use_face_split=False
        )

    # Remove degenerate faces.
    bad_faces = []
    for f in bm.faces:
        if not f.is_valid:
            continue
        if len(set(f.verts)) < 3 or f.calc_area() < 1e-12:
            bad_faces.append(f)
    if bad_faces:
        bmesh.ops.delete(bm, geom=bad_faces, context='FACES')

    # Recalculate normals consistently.
    valid_faces = [f for f in bm.faces if f.is_valid]
    if valid_faces:
        bmesh.ops.recalc_face_normals(bm, faces=valid_faces)

    # Selection: result only.
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    for f in bm.faces:
        f.select = False

    for e in restored_edges:
        if e is not None and e.is_valid:
            e.select = True
            for v in e.verts:
                v.select = True

    bm.normal_update()
    bmesh.update_edit_mesh(me, loop_triangles=True, destructive=True)
    me.update()

    # Force redraw.
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def restore_one_face(bm, bevel_face):
    """Face mode: restore one simple bevel face."""
    if len(bevel_face.verts) != 4:
        return False, "選択面が四角形ではありません", []

    # Side faces: adjacent faces across the two longest edges.
    es = sorted(bevel_face.edges, key=lambda e: e.calc_length(), reverse=True)
    long_edges = es[:2]

    side_faces = []
    for e in long_edges:
        others = [f for f in e.link_faces if f != bevel_face]
        if len(others) != 1:
            return False, "隣接面を特定できません", []
        side_faces.append(others[0])

    line = plane_line(side_faces[0], side_faces[1])
    if line is None:
        return False, "元エッジを計算できません", []

    # Short edges are the bevel width edges. Collapse both to the original line.
    short_edges = [e for e in bevel_face.edges if e not in long_edges]
    if len(short_edges) != 2:
        return False, "ベベル幅エッジを特定できません", []

    targets = []
    for e in short_edges:
        mid = (e.verts[0].co + e.verts[1].co) * 0.5
        targets.append(project_line(mid, line))

    bmesh.ops.delete(bm, geom=[bevel_face], context='FACES_ONLY')

    for e, t in zip(short_edges, targets):
        if not e.is_valid:
            continue
        e.verts[0].co = t
        e.verts[1].co = t
        bmesh.ops.pointmerge(bm, verts=list(e.verts), merge_co=t)

    # Find the restored edge between the two resulting positions.
    candidates = []
    for e in bm.edges:
        if not e.is_valid:
            continue
        a, b = e.verts
        da = min((a.co - targets[0]).length, (a.co - targets[1]).length)
        db = min((b.co - targets[0]).length, (b.co - targets[1]).length)
        if da < 1e-6 and db < 1e-6:
            candidates.append(e)

    return True, "復元しました", candidates


def restore_single_boundary_vertex(bm, v):
    """
    Vertex mode, one vertex:
    collapse only the local bevel width edge at the selected vertex.

    Important: keep the bevel face alive while welding, then remove only faces
    that actually became degenerate. This avoids inverted/red faces.
    """
    if not v.is_valid:
        return False, "頂点が無効です", []

    linked_edges = [e for e in v.link_edges if e.is_valid]
    if len(linked_edges) < 2:
        return False, "周辺トポロジーが不足しています", []

    candidates = []

    for cross in linked_edges:
        other = cross.other_vert(v)
        if not other.is_valid:
            continue

        common_faces = [f for f in cross.link_faces if f.is_valid]

        for bevel_face in common_faces:
            side_a = [
                f for f in v.link_faces
                if f.is_valid and f != bevel_face
            ]
            side_b = [
                f for f in other.link_faces
                if f.is_valid and f != bevel_face
            ]

            for fa in side_a:
                for fb in side_b:
                    if fa == fb:
                        continue

                    line = plane_line(fa, fb)
                    if line is None:
                        continue

                    mid = (v.co + other.co) * 0.5
                    target = project_line(mid, line)

                    move = (
                        (v.co - target).length +
                        (other.co - target).length
                    )

                    candidates.append({
                        "move": move,
                        "other": other,
                        "bevel_face": bevel_face,
                        "fa": fa,
                        "fb": fb,
                        "target": target,
                    })

    if not candidates:
        return False, "局所ベベル幅を特定できません", []

    c = min(candidates, key=lambda x: x["move"])
    other = c["other"]
    target = c["target"]

    # Keep faces while welding.
    v.co = target
    other.co = target

    verts_to_merge = [x for x in (v, other) if x.is_valid]
    if len(verts_to_merge) >= 2:
        bmesh.ops.remove_doubles(bm, verts=verts_to_merge, dist=1e-7)

    # Remove only local degenerate faces.
    local_faces = set()
    for x in (v, other):
        if x.is_valid:
            local_faces.update(f for f in x.link_faces if f.is_valid)

    bad_faces = [
        f for f in local_faces
        if len(set(f.verts)) < 3 or f.calc_area() < 1e-12
    ]
    if bad_faces:
        bmesh.ops.delete(bm, geom=bad_faces, context='FACES')

    # Pick edges touching the restored point so something sensible stays selected.
    restored = []
    for e in bm.edges:
        if not e.is_valid:
            continue
        if any((x.co - target).length < 1e-6 for x in e.verts):
            restored.append(e)

    return True, "局所的に復元しました", restored


def edges_from_selected_vertices(bm):
    """Vertex mode, 2+ vertices: convert selected vertex chain to edge chain."""
    selected = [v for v in bm.verts if v.select]
    if len(selected) < 2:
        return None, "2頂点以上選択してください"

    selected_set = set(selected)
    edges = [
        e for e in bm.edges
        if e.is_valid and e.verts[0] in selected_set and e.verts[1] in selected_set
    ]

    if not edges:
        return None, "選択頂点間にエッジがありません"

    # Every selected vertex must participate in the chain.
    used_verts = set()
    for e in edges:
        used_verts.update(e.verts)
    if used_verts != selected_set:
        return None, "選択頂点が1本のエッジ列になっていません"

    return edges, ""


def order_chain(edges):
    """Order a connected non-branching edge chain."""
    if not edges:
        return None

    edges = list(dict.fromkeys(edges))

    adj = {}
    for e in edges:
        for v in e.verts:
            adj.setdefault(v, []).append(e)

    if any(len(x) > 2 for x in adj.values()):
        return None

    ends = [v for v, es in adj.items() if len(es) == 1]
    if len(ends) not in (0, 2):
        return None

    start_v = ends[0] if ends else edges[0].verts[0]

    ordered = []
    used = set()
    current_v = start_v

    while len(used) < len(edges):
        nxt = None
        for e in adj[current_v]:
            if e not in used:
                nxt = e
                break
        if nxt is None:
            return None

        ordered.append(nxt)
        used.add(nxt)
        current_v = nxt.other_vert(current_v)

    return ordered


def opposite_edge(face, edge):
    """Find the topological opposite edge of edge inside face."""
    if not face.is_valid or not edge.is_valid:
        return None

    ev = set(edge.verts)
    candidates = [
        e for e in face.edges
        if e != edge and not (set(e.verts) & ev)
    ]

    if not candidates:
        return None

    # Usually exactly one for a quad. Prefer the most parallel edge if irregular.
    d0 = (edge.verts[1].co - edge.verts[0].co)
    if d0.length_squared < EPS:
        return candidates[0]
    d0.normalize()

    def score(e):
        d = e.verts[1].co - e.verts[0].co
        if d.length_squared < EPS:
            return -1.0
        d.normalize()
        return abs(d.dot(d0))

    return max(candidates, key=score)


def candidate_from_side(boundary_edge, bevel_face):
    """
    Starting from a selected boundary edge and one linked face as bevel candidate:
    selected boundary -> bevel face -> opposite edge -> far original side face.
    """
    if not boundary_edge.is_valid or not bevel_face.is_valid:
        return None

    near_faces = [f for f in boundary_edge.link_faces if f != bevel_face and f.is_valid]
    if len(near_faces) != 1:
        return None
    near_face = near_faces[0]

    opp = opposite_edge(bevel_face, boundary_edge)
    if opp is None:
        return None

    far_faces = [f for f in opp.link_faces if f != bevel_face and f.is_valid]
    if len(far_faces) != 1:
        return None
    far_face = far_faces[0]

    line = plane_line(near_face, far_face)
    if line is None:
        return None

    p, d = line

    # Project selected boundary endpoints to the reconstructed original edge.
    projected = [project_line(v.co, line) for v in boundary_edge.verts]
    movement = sum((v.co - q).length for v, q in zip(boundary_edge.verts, projected))

    # Direction agreement between selected edge and reconstructed line.
    ed = boundary_edge.verts[1].co - boundary_edge.verts[0].co
    if ed.length_squared < EPS:
        return None
    ed.normalize()
    parallel = abs(ed.dot(d))

    # Lower cost is better.
    cost = movement + (1.0 - parallel) * max(boundary_edge.calc_length(), 1.0)

    return {
        "boundary": boundary_edge,
        "bevel_face": bevel_face,
        "opposite": opp,
        "near_face": near_face,
        "far_face": far_face,
        "line": line,
        "cost": cost,
    }


def segment_candidates(edge):
    """All valid side interpretations for one selected boundary edge."""
    out = []
    for f in edge.link_faces:
        c = candidate_from_side(edge, f)
        if c is not None:
            out.append(c)
    return out


def choose_consistent_candidates(ordered_edges):
    """
    Dynamic programming: choose one bevel-side interpretation per selected edge,
    preferring topological continuity across the chain.
    """
    all_candidates = [segment_candidates(e) for e in ordered_edges]

    if any(len(cands) == 0 for cands in all_candidates):
        return None

    dp = []
    back = []

    for i, cands in enumerate(all_candidates):
        dp.append([float('inf')] * len(cands))
        back.append([-1] * len(cands))

        if i == 0:
            for j, c in enumerate(cands):
                dp[i][j] = c["cost"]
            continue

        prev_cands = all_candidates[i - 1]

        for j, c in enumerate(cands):
            for k, pc in enumerate(prev_cands):
                transition = 0.0

                # Strongly prefer bevel faces that continue topologically.
                shared_bevel_verts = set(c["bevel_face"].verts) & set(pc["bevel_face"].verts)
                if not shared_bevel_verts:
                    transition += 1000.0

                # Prefer same near-side surface continuity.
                shared_near_verts = set(c["near_face"].verts) & set(pc["near_face"].verts)
                if not shared_near_verts:
                    transition += 100.0

                # Prefer same far-side surface continuity.
                shared_far_verts = set(c["far_face"].verts) & set(pc["far_face"].verts)
                if not shared_far_verts:
                    transition += 100.0

                # Prefer reconstructed line directions that agree.
                d1 = c["line"][1]
                d2 = pc["line"][1]
                transition += (1.0 - abs(d1.dot(d2))) * 10.0

                val = dp[i - 1][k] + c["cost"] + transition
                if val < dp[i][j]:
                    dp[i][j] = val
                    back[i][j] = k

    j = min(range(len(dp[-1])), key=lambda x: dp[-1][x])
    chosen = [None] * len(ordered_edges)

    for i in range(len(ordered_edges) - 1, -1, -1):
        chosen[i] = all_candidates[i][j]
        j = back[i][j] if i > 0 else -1

    return chosen


def restore_edge_chain(bm, selected_edges):
    """Edge mode / multi-vertex mode: restore one selected bevel boundary chain."""
    ordered = order_chain(selected_edges)
    if ordered is None:
        return False, "選択エッジが1本の連続した非分岐チェーンではありません", []

    chosen = choose_consistent_candidates(ordered)
    if chosen is None:
        return False, "ベベル面と元の面を特定できません", []

    # Each bevel face should normally be used once. Duplicate use is okay at
    # unusual topology, so keep unique faces only for deletion.
    bevel_faces = []
    for c in chosen:
        f = c["bevel_face"]
        if f not in bevel_faces:
            bevel_faces.append(f)

    # Collect cross-section pairs. Each selected boundary vertex should collapse
    # with the corresponding opposite-side vertex to the plane intersection.
    merge_groups = []
    target_positions = []

    for c in chosen:
        be = c["boundary"]
        oe = c["opposite"]
        line = c["line"]

        # Pair endpoints by nearest distance. Works regardless of edge direction.
        bvs = list(be.verts)
        ovs = list(oe.verts)

        pair_a = [
            (bvs[0], ovs[0], (bvs[0].co - ovs[0].co).length + (bvs[1].co - ovs[1].co).length),
            (bvs[0], ovs[1], (bvs[0].co - ovs[1].co).length + (bvs[1].co - ovs[0].co).length),
        ]

        if pair_a[0][2] <= pair_a[1][2]:
            pairs = [(bvs[0], ovs[0]), (bvs[1], ovs[1])]
        else:
            pairs = [(bvs[0], ovs[1]), (bvs[1], ovs[0])]

        for bv, ov in pairs:
            mid = (bv.co + ov.co) * 0.5
            target = project_line(mid, line)
            merge_groups.append((bv, ov))
            target_positions.append(target)

    # Delete bevel strip faces first.
    valid_bevel_faces = [f for f in bevel_faces if f.is_valid]
    if valid_bevel_faces:
        bmesh.ops.delete(bm, geom=valid_bevel_faces, context='FACES_ONLY')

    # Multiple segments share vertices, so accumulate target positions per vertex.
    accum = {}
    for (bv, ov), target in zip(merge_groups, target_positions):
        if bv.is_valid:
            accum.setdefault(bv, []).append(target)
        if ov.is_valid:
            accum.setdefault(ov, []).append(target)

    for v, positions in accum.items():
        if not v.is_valid:
            continue
        avg = positions[0].copy()
        for p in positions[1:]:
            avg += p
        avg /= len(positions)
        v.co = avg

    # Weld vertices that landed on the same original edge positions.
    valid_verts = [v for v in accum if v.is_valid]
    if valid_verts:
        bmesh.ops.remove_doubles(bm, verts=valid_verts, dist=1e-7)

    # Find restored edges near target positions.
    unique_targets = []
    for p in target_positions:
        if not any((p - q).length < 1e-6 for q in unique_targets):
            unique_targets.append(p)

    restored = []
    for e in bm.edges:
        if not e.is_valid:
            continue
        if all(any((v.co - p).length < 1e-5 for p in unique_targets) for v in e.verts):
            restored.append(e)

    return True, "復元しました", restored


class MESH_OT_restore_bevel(bpy.types.Operator):
    bl_idname = "mesh.restore_bevel"
    bl_label = "Restore Bevel Edge"
    bl_description = "適用済みベベルを元の鋭いエッジへ復元します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_MESH'
            and context.edit_object is not None
            and context.edit_object.type == 'MESH'
        )

    def execute(self, context):
        obj = context.edit_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)

        vertex_mode = context.tool_settings.mesh_select_mode[0]
        edge_mode = context.tool_settings.mesh_select_mode[1]
        face_mode = context.tool_settings.mesh_select_mode[2]

        if face_mode:
            fs = [f for f in bm.faces if f.select]
            if len(fs) != 1:
                self.report({'ERROR'}, "面選択ではベベル面を1枚だけ選択してください")
                return {'CANCELLED'}
            ok, msg, restored = restore_one_face(bm, fs[0])

        elif edge_mode:
            es = [e for e in bm.edges if e.select]
            if not es:
                self.report({'ERROR'}, "ベベル片側の境界エッジ列を選択してください")
                return {'CANCELLED'}
            ok, msg, restored = restore_edge_chain(bm, es)

        elif vertex_mode:
            vs = [v for v in bm.verts if v.select]

            if len(vs) == 1:
                ok, msg, restored = restore_single_boundary_vertex(bm, vs[0])
            else:
                es, msg = edges_from_selected_vertices(bm)
                if es is None:
                    self.report({'ERROR'}, msg)
                    return {'CANCELLED'}
                ok, msg, restored = restore_edge_chain(bm, es)

        else:
            self.report({'ERROR'}, "頂点・辺・面のいずれかの選択モードで実行してください")
            return {'CANCELLED'}

        if not ok:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        refresh(context, bm, me, restored)
        self.report({'INFO'}, "ベベルを元のエッジへ復元しました")
        return {'FINISHED'}


def menu_func(self, context):
    self.layout.separator()
    self.layout.operator(
        MESH_OT_restore_bevel.bl_idname,
        text="Restore Bevel Edge"
    )


translations_dict = {
    "ja_JP": {
        ("*", "Restore Bevel Edge"): "ベベルを元のエッジに戻す",
        ("Operator", "Restore Bevel Edge"): "ベベルを元のエッジに戻す",
    },
}


classes = (MESH_OT_restore_bevel,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.app.translations.register(__name__, translations_dict)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(menu_func)
    bpy.app.translations.unregister(__name__)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
