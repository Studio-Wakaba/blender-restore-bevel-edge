bl_info = {
    "name": "Restore Bevel Edge",
    "author": "STUDIO WAKABA",
    "version": (1, 2, 0),
    "blender": (5, 2, 0),
    "location": "Edit Mode > Context Menu",
    "description": "Restore applied bevel geometry from face, edge, vertex chain, or a single boundary vertex",
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


def refresh(bm, me, keep_edges=None, keep_verts=None):
    keep_edges = keep_edges or []
    keep_verts = keep_verts or []

    # Remove zero-length edges.
    zero_edges = [e for e in bm.edges if e.is_valid and (e.verts[0].co - e.verts[1].co).length_squared < EPS]
    if zero_edges:
        bmesh.ops.dissolve_edges(bm, edges=zero_edges, use_verts=True, use_face_split=False)

    # Remove degenerate faces that may remain after local welding.
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

    for e in keep_edges:
        if e and e.is_valid:
            e.select = True
            for v in e.verts:
                v.select = True
    for v in keep_verts:
        if v and v.is_valid:
            v.select = True

    bm.normal_update()
    bmesh.update_edit_mesh(me, loop_triangles=True, destructive=True)
    me.update()

    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def restore_one_face(bm, me, bevel_face):
    if not bevel_face or not bevel_face.is_valid:
        return False, "Selected face is invalid."

    # For a simple applied bevel face, the two long boundary edges usually each
    # touch one original side face. Pick the two neighboring faces whose normals
    # differ the most and reconstruct their plane intersection.
    neighbors = []
    for e in bevel_face.edges:
        for f in e.link_faces:
            if f != bevel_face and f.is_valid and f not in neighbors:
                neighbors.append(f)

    if len(neighbors) < 2:
        return False, "Could not find two neighboring side faces."

    best = None
    best_score = -1.0
    for i in range(len(neighbors)):
        for j in range(i + 1, len(neighbors)):
            n1 = neighbors[i].normal.normalized()
            n2 = neighbors[j].normal.normalized()
            score = 1.0 - abs(n1.dot(n2))
            if score > best_score:
                line = plane_line(neighbors[i], neighbors[j])
                if line:
                    best_score = score
                    best = (neighbors[i], neighbors[j], line)

    if not best:
        return False, "Could not reconstruct the original edge line."

    f1, f2, line = best

    # Find the two bevel-face edges bordering the chosen side faces.
    side_edges = []
    for side in (f1, f2):
        candidates = [e for e in bevel_face.edges if side in e.link_faces]
        if candidates:
            side_edges.append(max(candidates, key=lambda e: e.calc_length()))

    if len(side_edges) != 2:
        return False, "Could not identify the bevel boundaries."

    # Collapse each end pair to the reconstructed line.
    verts = list(bevel_face.verts)
    targets = {v: project_line(v.co, line) for v in verts}

    # Pair vertices by closeness along the line parameter.
    p, d = line
    ordered = sorted(verts, key=lambda v: (v.co - p).dot(d))
    if len(ordered) < 4:
        return False, "The selected bevel face is too simple to restore."

    half = len(ordered) // 2
    groups = [ordered[:half], ordered[half:]]
    restored_verts = []
    for group in groups:
        if not group:
            continue
        avg = sum((targets[v] for v in group), group[0].co.copy() * 0.0) / len(group)
        for v in group:
            v.co = avg
        bmesh.ops.remove_doubles(bm, verts=group, dist=1e-7)
        restored_verts.extend(group)

    refresh(bm, me, keep_verts=restored_verts)
    return True, "Restored bevel face to the original edge."


def restore_single_boundary_vertex(bm, me, v):
    if not v or not v.is_valid:
        return False, "Selected vertex is invalid."

    # A boundary vertex of an applied bevel strip typically belongs to the bevel
    # face and one original side face. The cross-section edge leads to the other
    # side of the bevel. Keep the faces alive while welding so their winding can
    # be repaired consistently afterwards.
    linked_edges = [e for e in v.link_edges if e.is_valid]
    if len(linked_edges) < 2:
        return False, "Not enough topology around the selected vertex."

    candidates = []
    for cross in linked_edges:
        other = cross.other_vert(v)
        common_faces = [f for f in cross.link_faces if f.is_valid]
        for bevel_face in common_faces:
            # Side face at selected vertex, excluding the candidate bevel face.
            side_a = [f for f in v.link_faces if f.is_valid and f != bevel_face]
            side_b = [f for f in other.link_faces if f.is_valid and f != bevel_face]
            for fa in side_a:
                for fb in side_b:
                    if fa == fb:
                        continue
                    line = plane_line(fa, fb)
                    if not line:
                        continue
                    target = project_line((v.co + other.co) * 0.5, line)
                    movement = (v.co - target).length + (other.co - target).length
                    candidates.append((movement, other, bevel_face, fa, fb, target))

    if not candidates:
        return False, "Could not determine the local bevel cross-section."

    _, other, bevel_face, fa, fb, target = min(candidates, key=lambda x: x[0])

    # Do not delete the bevel face before welding. Keeping it in the topology
    # avoids leaving neighboring faces with inconsistent local winding.
    v.co = target
    other.co = target
    bmesh.ops.remove_doubles(bm, verts=[v, other], dist=1e-7)

    # Remove only faces that actually became degenerate as a result of the weld.
    local_faces = set()
    if v.is_valid:
        local_faces.update(f for f in v.link_faces if f.is_valid)
    if other.is_valid:
        local_faces.update(f for f in other.link_faces if f.is_valid)
    bad_faces = [f for f in local_faces if len(set(f.verts)) < 3 or f.calc_area() < 1e-12]
    if bad_faces:
        bmesh.ops.delete(bm, geom=bad_faces, context='FACES')

    keep = v if v.is_valid else (other if other.is_valid else None)
    refresh(bm, me, keep_verts=[keep] if keep else [])
    return True, "Restored the bevel locally at the selected vertex."


def edges_from_selected_vertices(bm):
    selected = {v for v in bm.verts if v.select}
    return [e for e in bm.edges if e.is_valid and e.verts[0] in selected and e.verts[1] in selected]


def order_chain(edges):
    if not edges:
        return []

    adj = {}
    for e in edges:
        for v in e.verts:
            adj.setdefault(v, []).append(e)

    if any(len(es) > 2 for es in adj.values()):
        return []

    ends = [v for v, es in adj.items() if len(es) == 1]
    start = ends[0] if ends else edges[0].verts[0]

    ordered = []
    used = set()
    current_v = start
    while True:
        next_edges = [e for e in adj.get(current_v, []) if e not in used]
        if not next_edges:
            break
        e = next_edges[0]
        used.add(e)
        ordered.append(e)
        current_v = e.other_vert(current_v)

    if len(ordered) != len(edges):
        return []
    return ordered


def opposite_edge(face, edge):
    if not face or not face.is_valid or edge not in face.edges:
        return None

    # Prefer an edge sharing no vertices with the selected boundary edge.
    ev = set(edge.verts)
    candidates = [e for e in face.edges if e != edge and not ev.intersection(e.verts)]
    if candidates:
        return max(candidates, key=lambda e: e.calc_length())

    # Fallback for triangles / irregular topology: choose the edge farthest from
    # the selected edge midpoint.
    mid = (edge.verts[0].co + edge.verts[1].co) * 0.5
    others = [e for e in face.edges if e != edge]
    if not others:
        return None
    return max(others, key=lambda e: (((e.verts[0].co + e.verts[1].co) * 0.5) - mid).length_squared)


def candidate_from_side(boundary_edge, bevel_face):
    if bevel_face not in boundary_edge.link_faces:
        return None

    opp = opposite_edge(bevel_face, boundary_edge)
    if not opp:
        return None

    beyond = [f for f in opp.link_faces if f != bevel_face and f.is_valid]
    if not beyond:
        return None
    far_face = beyond[0]

    near_faces = [f for f in boundary_edge.link_faces if f != bevel_face and f.is_valid]
    if not near_faces:
        return None
    near_face = near_faces[0]

    line = plane_line(near_face, far_face)
    if not line:
        return None

    # Score favors a strip-like bevel face and modest movement to the recovered line.
    p, d = line
    move = sum((v.co - project_line(v.co, line)).length for v in boundary_edge.verts)
    normal_change = 1.0 - abs(near_face.normal.normalized().dot(far_face.normal.normalized()))

    return {
        'boundary': boundary_edge,
        'bevel_face': bevel_face,
        'opposite': opp,
        'near_face': near_face,
        'far_face': far_face,
        'line': line,
        'move': move,
        'normal_change': normal_change,
    }


def segment_candidates(edge):
    out = []
    for f in edge.link_faces:
        c = candidate_from_side(edge, f)
        if c:
            out.append(c)
    return out


def choose_consistent_candidates(chain):
    all_cands = [segment_candidates(e) for e in chain]
    if any(not cs for cs in all_cands):
        return None

    # Dynamic programming across the chain. Reward neighboring candidates whose
    # bevel faces share topology and whose reconstructed directions are similar.
    dp = []
    back = []

    for i, cs in enumerate(all_cands):
        row = []
        brow = []
        for j, c in enumerate(cs):
            base = c['move'] - 0.05 * c['normal_change']
            if i == 0:
                row.append(base)
                brow.append(None)
                continue

            best_cost = None
            best_k = None
            for k, prev in enumerate(all_cands[i - 1]):
                cost = dp[i - 1][k] + base

                # Prefer bevel faces that touch/share an edge or vertex.
                shared_verts = set(c['bevel_face'].verts).intersection(prev['bevel_face'].verts)
                if shared_verts:
                    cost -= 0.25
                else:
                    cost += 0.25

                d1 = c['line'][1]
                d2 = prev['line'][1]
                cost += (1.0 - abs(d1.dot(d2))) * 0.2

                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best_k = k

            row.append(best_cost)
            brow.append(best_k)
        dp.append(row)
        back.append(brow)

    j = min(range(len(dp[-1])), key=lambda x: dp[-1][x])
    chosen = [None] * len(chain)
    for i in range(len(chain) - 1, -1, -1):
        chosen[i] = all_cands[i][j]
        if i > 0:
            j = back[i][j]
    return chosen


def restore_edge_chain(bm, me, selected_edges):
    chain = order_chain(selected_edges)
    if not chain:
        return False, "Selection must form one continuous, non-branching edge chain."

    chosen = choose_consistent_candidates(chain)
    if not chosen:
        return False, "Could not identify a consistent bevel strip from the selected boundary."

    # Project every selected boundary vertex onto the recovered original edge line.
    # At joints, average projections from adjacent segments.
    accum = {}
    for c in chosen:
        line = c['line']
        e = c['boundary']
        for v in e.verts:
            accum.setdefault(v, []).append(project_line(v.co, line))

    for v, targets in accum.items():
        avg = sum(targets, targets[0].copy() * 0.0) / len(targets)
        v.co = avg

    # Move opposite-side vertices onto the same recovered line, then weld each
    # bevel cross-section. This removes the applied bevel strip.
    weld_pairs = []
    for c in chosen:
        b = c['boundary']
        opp = c['opposite']
        line = c['line']

        for bv in b.verts:
            # Match the opposite-edge endpoint by nearest position.
            ov = min(opp.verts, key=lambda x: (x.co - bv.co).length_squared)
            target = project_line((bv.co + ov.co) * 0.5, line)
            bv.co = target
            ov.co = target
            weld_pairs.append((bv, ov))

    # Remove duplicate bevel faces only after positioning all segments.
    bevel_faces = list({c['bevel_face'] for c in chosen if c['bevel_face'].is_valid})
    if bevel_faces:
        bmesh.ops.delete(bm, geom=bevel_faces, context='FACES_ONLY')

    weld_verts = []
    for a, b in weld_pairs:
        if a.is_valid:
            weld_verts.append(a)
        if b.is_valid:
            weld_verts.append(b)
    if weld_verts:
        bmesh.ops.remove_doubles(bm, verts=list(set(weld_verts)), dist=1e-7)

    restored = [e for e in bm.edges if e.is_valid and e.select]
    refresh(bm, me, keep_edges=restored, keep_verts=list(accum.keys()))
    return True, "Restored bevel strip to the original edge."


class MESH_OT_restore_bevel(bpy.types.Operator):
    bl_idname = "mesh.restore_bevel"
    bl_label = "ベベルを元のエッジに戻す"
    bl_description = "Restore an already-applied bevel to the original sharp edge"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.edit_object is not None

    def execute(self, context):
        obj = context.edit_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.normal_update()

        mode = context.tool_settings.mesh_select_mode
        selected_faces = [f for f in bm.faces if f.select]
        selected_edges = [e for e in bm.edges if e.select]
        selected_verts = [v for v in bm.verts if v.select]

        ok = False
        msg = "Nothing to restore."

        if mode[2]:
            if len(selected_faces) != 1:
                self.report({'WARNING'}, "Select exactly one bevel face.")
                return {'CANCELLED'}
            ok, msg = restore_one_face(bm, me, selected_faces[0])

        elif mode[1]:
            if not selected_edges:
                self.report({'WARNING'}, "Select one boundary edge or an edge chain along one side of the bevel.")
                return {'CANCELLED'}
            ok, msg = restore_edge_chain(bm, me, selected_edges)

        elif mode[0]:
            if len(selected_verts) == 1:
                ok, msg = restore_single_boundary_vertex(bm, me, selected_verts[0])
            else:
                chain_edges = edges_from_selected_vertices(bm)
                if not chain_edges:
                    self.report({'WARNING'}, "Select one boundary vertex, or 2+ connected vertices along one side of the bevel.")
                    return {'CANCELLED'}
                ok, msg = restore_edge_chain(bm, me, chain_edges)

        else:
            self.report({'WARNING'}, "Use vertex, edge, or face select mode.")
            return {'CANCELLED'}

        self.report({'INFO'} if ok else {'WARNING'}, msg)
        return {'FINISHED'} if ok else {'CANCELLED'}


def menu_func(self, context):
    self.layout.separator()
    self.layout.operator(MESH_OT_restore_bevel.bl_idname, icon='MOD_BEVEL')


classes = (
    MESH_OT_restore_bevel,
)


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
