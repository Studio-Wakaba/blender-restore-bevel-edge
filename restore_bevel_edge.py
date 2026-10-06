bl_info = {
    "name": "Restore Bevel Edge",
    "author": "STUDIO WAKABA",
    "version": (1, 2, 3),
    "blender": (5, 2, 0),
    "location": "Edit Mode > Context Menu",
    "description": "Restore applied bevel geometry from face, edge, vertex chain, or a single boundary vertex",
    "category": "Mesh",
}

import bpy
import bmesh
from mathutils.geometry import intersect_plane_plane

EPS = 1e-10


def is_japanese():
    locale = bpy.app.translations.locale or ""
    return locale.lower().startswith("ja")


def tr(ja, en):
    return ja if is_japanese() else en


def plane_line(f1, f2):
    r = intersect_plane_plane(
        f1.calc_center_median(), f1.normal,
        f2.calc_center_median(), f2.normal
    )
    if not r:
        return None
    p, d = r
    # Blender returns (None, None) when the two planes do not have a
    # usable intersection line (for example, parallel/coplanar planes).
    if p is None or d is None:
        return None
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

    bad_faces = []
    for f in bm.faces:
        if not f.is_valid:
            continue
        if len(set(f.verts)) < 3 or f.calc_area() < 1e-12:
            bad_faces.append(f)
    if bad_faces:
        bmesh.ops.delete(bm, geom=bad_faces, context='FACES')

    valid_faces = [f for f in bm.faces if f.is_valid]
    if valid_faces:
        bmesh.ops.recalc_face_normals(bm, faces=valid_faces)

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

    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def restore_one_face(bm, bevel_face):
    if len(bevel_face.verts) != 4:
        return False, tr("選択面が四角形ではありません", "The selected face is not a quad"), []

    edges = list(bevel_face.edges)
    best = None
    for i in range(4):
        for j in range(i + 1, 4):
            e1, e2 = edges[i], edges[j]
            if set(e1.verts) & set(e2.verts):
                continue
            f1s = [f for f in e1.link_faces if f != bevel_face and f.is_valid]
            f2s = [f for f in e2.link_faces if f != bevel_face and f.is_valid]
            if len(f1s) != 1 or len(f2s) != 1:
                continue
            f1, f2 = f1s[0], f2s[0]
            line = plane_line(f1, f2)
            if line is None:
                continue
            move = sum((v.co - project_line(v.co, line)).length for v in bevel_face.verts)
            if best is None or move < best[0]:
                best = (move, e1, e2, line)

    if best is None:
        return False, tr("元エッジを計算できません", "Could not calculate the original edge"), []

    _, long1, long2, line = best
    short_edges = [e for e in bevel_face.edges if e not in (long1, long2)]
    if len(short_edges) != 2:
        return False, tr("ベベル幅エッジを特定できません", "Could not identify the bevel width edges"), []

    targets = []
    for e in short_edges:
        mid = (e.verts[0].co + e.verts[1].co) * 0.5
        targets.append(project_line(mid, line))

    bmesh.ops.delete(bm, geom=[bevel_face], context='FACES_ONLY')

    for e, t in zip(short_edges, targets):
        if not e.is_valid:
            continue
        verts = [v for v in e.verts if v.is_valid]
        for v in verts:
            v.co = t
        if len(verts) >= 2:
            bmesh.ops.pointmerge(bm, verts=verts, merge_co=t)

    candidates = []
    for e in bm.edges:
        if not e.is_valid:
            continue
        a, b = e.verts
        da = min((a.co - targets[0]).length, (a.co - targets[1]).length)
        db = min((b.co - targets[0]).length, (b.co - targets[1]).length)
        if da < 1e-6 and db < 1e-6:
            candidates.append(e)

    return True, "", candidates


def restore_single_boundary_vertex(bm, selected):
    if not selected.is_valid:
        return False, tr("選択頂点が無効です", "The selected vertex is invalid"), []

    candidates = []
    for edge in selected.link_edges:
        if not edge.is_valid:
            continue
        other = edge.other_vert(selected)
        if not other.is_valid:
            continue

        common_faces = [f for f in edge.link_faces if f.is_valid]
        for bevel_face in common_faces:
            side_a = [f for f in selected.link_faces if f.is_valid and f != bevel_face]
            side_b = [f for f in other.link_faces if f.is_valid and f != bevel_face]
            for fa in side_a:
                for fb in side_b:
                    if fa == fb:
                        continue
                    line = plane_line(fa, fb)
                    if line is None:
                        continue
                    mid = (selected.co + other.co) * 0.5
                    target = project_line(mid, line)
                    move = (selected.co - target).length + (other.co - target).length
                    candidates.append((move, other, target))

    if not candidates:
        return False, tr("局所ベベル幅を特定できません", "Could not identify the local bevel width"), []

    _, other, target = min(candidates, key=lambda x: x[0])

    selected.co = target
    other.co = target
    verts = [v for v in (selected, other) if v.is_valid]
    if len(verts) >= 2:
        bmesh.ops.remove_doubles(bm, verts=verts, dist=1e-7)

    bad_faces = []
    for f in bm.faces:
        if not f.is_valid:
            continue
        if len(set(f.verts)) < 3 or f.calc_area() < 1e-12:
            bad_faces.append(f)
    if bad_faces:
        bmesh.ops.delete(bm, geom=bad_faces, context='FACES')

    restored = []
    for e in bm.edges:
        if e.is_valid and any((v.co - target).length < 1e-6 for v in e.verts):
            restored.append(e)

    return True, "", restored


def edges_from_selected_vertices(bm):
    selected = [v for v in bm.verts if v.select]
    if len(selected) < 2:
        return None, tr("ベベル片側の境界頂点を2個以上選択してください", "Select at least two boundary vertices along one side of the bevel")

    selected_set = set(selected)
    edges = [
        e for e in bm.edges
        if e.is_valid and e.verts[0] in selected_set and e.verts[1] in selected_set
    ]
    if not edges:
        return None, tr("選択頂点をつなぐエッジがありません", "No edges connect the selected vertices")

    used = set()
    for e in edges:
        used.update(e.verts)
    if used != selected_set:
        return None, tr("選択頂点は1本の連続した境界頂点列にしてください", "Selected vertices must form one continuous boundary chain")

    chain, msg = order_chain(edges)
    if chain is None:
        return None, msg
    return chain, ""


def order_chain(edges):
    edges = list(dict.fromkeys(e for e in edges if e.is_valid))
    if not edges:
        return None, tr("エッジが選択されていません", "No edges are selected")

    adjacency = {}
    for e in edges:
        for v in e.verts:
            adjacency.setdefault(v, []).append(e)

    if any(len(es) > 2 for es in adjacency.values()):
        return None, tr("選択エッジが分岐しています", "The selected edge chain is branching")

    ends = [v for v, es in adjacency.items() if len(es) == 1]
    if len(edges) == 1:
        return edges, ""
    if len(ends) != 2:
        return None, tr("片側の開いた連続エッジ列を選択してください", "Select an open continuous edge chain along one side of the bevel")

    ordered = []
    pv, pe = ends[0], None
    while len(ordered) < len(edges):
        next_edges = [e for e in adjacency[pv] if e != pe and e not in ordered]
        if not next_edges:
            break
        e = next_edges[0]
        ordered.append(e)
        nv = e.other_vert(pv)
        pe, pv = e, nv

    if len(ordered) != len(edges):
        return None, tr("選択エッジ列をたどれません", "Could not follow the selected edge chain")
    return ordered, ""


def opposite_edge(face, edge):
    if not face.is_valid or not edge.is_valid:
        return None
    ev = set(edge.verts)
    candidates = [e for e in face.edges if e != edge and not (set(e.verts) & ev)]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.calc_length())


def candidate_from_side(edge, bevel_face):
    if not edge.is_valid or not bevel_face.is_valid:
        return None

    outside = [f for f in edge.link_faces if f.is_valid and f != bevel_face]
    if len(outside) != 1:
        return None
    outside_face = outside[0]

    oe = opposite_edge(bevel_face, edge)
    if oe is None:
        return None

    far_faces = [f for f in oe.link_faces if f.is_valid and f != bevel_face]
    if len(far_faces) != 1:
        return None

    best = None
    for far in far_faces:
        line = plane_line(outside_face, far)
        if line is None:
            continue
        move = sum((v.co - project_line(v.co, line)).length for v in edge.verts)
        if best is None or move < best[0]:
            best = (move, far, line)

    if best is None:
        return None

    move, far_face, line = best
    return {
        "boundary": edge,
        "bevel_face": bevel_face,
        "opposite": oe,
        "near_face": outside_face,
        "far_face": far_face,
        "line": line,
        "cost": move,
    }


def segment_candidates(edge):
    result = []
    for f in edge.link_faces:
        if not f.is_valid:
            continue
        c = candidate_from_side(edge, f)
        if c is not None:
            result.append(c)
    return result


def choose_consistent_candidates(ordered_edges):
    allc = [segment_candidates(e) for e in ordered_edges]
    if any(not c for c in allc):
        return None

    dp = []
    back = []
    for i, cands in enumerate(allc):
        dp.append([float('inf')] * len(cands))
        back.append([-1] * len(cands))
        if i == 0:
            for j, c in enumerate(cands):
                dp[i][j] = c["cost"]
            continue

        for j, c in enumerate(cands):
            for k, pc in enumerate(allc[i - 1]):
                transition = 0.0
                if not (set(c["bevel_face"].verts) & set(pc["bevel_face"].verts)):
                    transition += 1000.0
                if not (set(c["near_face"].verts) & set(pc["near_face"].verts)):
                    transition += 100.0
                if not (set(c["far_face"].verts) & set(pc["far_face"].verts)):
                    transition += 100.0
                d1 = c["line"][1]
                d2 = pc["line"][1]
                transition += (1.0 - abs(d1.dot(d2))) * 10.0
                value = dp[i - 1][k] + c["cost"] + transition
                if value < dp[i][j]:
                    dp[i][j] = value
                    back[i][j] = k

    j = min(range(len(dp[-1])), key=lambda x: dp[-1][x])
    chosen = [None] * len(allc)
    for i in range(len(allc) - 1, -1, -1):
        chosen[i] = allc[i][j]
        if i > 0:
            j = back[i][j]
    return chosen


def restore_edge_chain(bm, selected_edges):
    ordered, msg = order_chain(selected_edges)
    if ordered is None:
        return False, msg, []

    chosen = choose_consistent_candidates(ordered)
    if chosen is None:
        return False, tr("ベベル片側の境界エッジとして認識できません", "Could not recognize the selection as a boundary edge chain along one side of the bevel"), []

    if len(ordered) == 1:
        boundary_path = [ordered[0].verts[0], ordered[0].verts[1]]
    else:
        shared = set(ordered[0].verts) & set(ordered[1].verts)
        if not shared:
            return False, tr("選択エッジ列が連続していません", "The selected edge chain is not continuous"), []
        shared_v = next(iter(shared))
        boundary_path = [ordered[0].other_vert(shared_v), shared_v]
        current = shared_v
        for e in ordered[1:]:
            current = e.other_vert(current)
            boundary_path.append(current)

    opposite_path = []
    for i, c in enumerate(chosen):
        oe = c["opposite"]
        if i == 0:
            b0, b1 = boundary_path[0], boundary_path[1]
            o0, o1 = oe.verts
            same = (o0.co - b0.co).length + (o1.co - b1.co).length
            flip = (o1.co - b0.co).length + (o0.co - b1.co).length
            if same <= flip:
                opposite_path = [o0, o1]
            else:
                opposite_path = [o1, o0]
        else:
            last = opposite_path[-1]
            if oe.verts[0] == last:
                opposite_path.append(oe.verts[1])
            elif oe.verts[1] == last:
                opposite_path.append(oe.verts[0])
            else:
                a, b = oe.verts
                opposite_path.append(a if (a.co - last.co).length <= (b.co - last.co).length else b)

    if len(opposite_path) != len(boundary_path):
        return False, tr("反対側の境界頂点列を取得できません", "Could not determine the opposite boundary vertex chain"), []

    targets = []
    for i, (bv, ov) in enumerate(zip(boundary_path, opposite_path)):
        mid = (bv.co + ov.co) * 0.5
        lines = []
        if i > 0:
            lines.append(chosen[i - 1]["line"])
        if i < len(chosen):
            lines.append(chosen[i]["line"])
        qs = [project_line(mid, line) for line in lines]
        target = sum(qs, qs[0] * 0.0) / len(qs)
        targets.append(target)

    bevel_faces = list({c["bevel_face"] for c in chosen if c["bevel_face"].is_valid})
    if bevel_faces:
        bmesh.ops.delete(bm, geom=bevel_faces, context='FACES_ONLY')

    merged_path = []
    for bv, ov, target in zip(boundary_path, opposite_path, targets):
        verts = []
        if bv.is_valid:
            verts.append(bv)
        if ov.is_valid and ov not in verts:
            verts.append(ov)
        if not verts:
            continue
        for v in verts:
            v.co = target
        if len(verts) >= 2:
            bmesh.ops.pointmerge(bm, verts=verts, merge_co=target)

        survivor = min(
            (v for v in bm.verts if v.is_valid),
            key=lambda v: (v.co - target).length
        )
        merged_path.append(survivor)

    path = []
    for v in merged_path:
        if not path or v != path[-1]:
            path.append(v)

    restored = []
    for a, b in zip(path, path[1:]):
        if a == b:
            continue
        e = bm.edges.get((a, b))
        if not e:
            try:
                e = bm.edges.new((a, b))
            except ValueError:
                e = bm.edges.get((a, b))
        if e:
            restored.append(e)

    return True, "", restored


class MESH_OT_restore_bevel(bpy.types.Operator):
    bl_idname = "mesh.restore_bevel"
    bl_label = "Restore Bevel Edge"
    bl_description = "Restore applied bevel geometry from a face, boundary edge chain, boundary vertex chain, or a single boundary vertex"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        o = context.edit_object
        return o is not None and o.type == 'MESH'

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
                self.report({'ERROR'}, tr("面選択ではベベル面を1枚だけ選択してください", "In Face Select mode, select exactly one bevel face"))
                return {'CANCELLED'}
            ok, msg, restored = restore_one_face(bm, fs[0])

        elif edge_mode:
            es = [e for e in bm.edges if e.select]
            if not es:
                self.report({'ERROR'}, tr("ベベル片側の境界エッジ列を選択してください", "Select a boundary edge chain along one side of the bevel"))
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
            self.report({'ERROR'}, tr("頂点・辺・面のいずれかの選択モードで実行してください", "Run this in Vertex, Edge, or Face Select mode"))
            return {'CANCELLED'}

        if not ok:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        refresh(context, bm, me, restored)
        self.report({'INFO'}, tr("ベベルを元のエッジへ復元しました", "Bevel restored to the original edge"))
        return {'FINISHED'}


def menu_func(self, context):
    self.layout.separator()
    label = tr("ベベルを元のエッジに戻す", "Restore Bevel Edge")
    self.layout.operator(
        MESH_OT_restore_bevel.bl_idname,
        text=label
    )


classes = (MESH_OT_restore_bevel,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(menu_func)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
