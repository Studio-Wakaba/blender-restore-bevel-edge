# Restore Bevel Edge

A Blender add-on that reconstructs the original sharp edge from already-applied bevel geometry.

**Version:** 1.2.0  
**Blender:** 5.2+

## Features

- Restore an applied bevel from a selected bevel face.
- Restore a bevel strip from a boundary edge or connected edge chain.
- Restore a bevel strip from 2 or more connected boundary vertices.
- Restore a local bevel point from a single selected boundary vertex.
- Reconstructs the original edge from the intersection of the adjacent face planes.
- Supports Undo.

## Installation

1. Download `restore_bevel_edge.py` from this repository.
2. Open Blender.
3. Open **Edit > Preferences > Add-ons**.
4. Install the add-on from disk and select `restore_bevel_edge.py`.
5. Enable **Restore Bevel Edge**.

## Usage

1. Select the mesh and enter **Edit Mode**.
2. Select the bevel geometry using Face, Edge, or Vertex Select mode.
3. Right-click in the 3D Viewport.
4. Choose **ベベルを元のエッジに戻す** (Restore Bevel Edge).

### Face Select

Select exactly one bevel face.

### Edge Select

Select one boundary edge, or a continuous non-branching edge chain along one side of the bevel.

### Vertex Select

- Select one boundary vertex to restore the bevel locally at that point.
- Select 2 or more connected boundary vertices to restore a bevel strip.

## How it works

Restore Bevel Edge estimates the original sharp edge by finding the intersection line of the two adjacent original face planes. The bevel geometry is then projected and welded back onto that reconstructed line.

## Limitations

The surrounding topology must still contain enough information to reconstruct the original adjacent planes. Highly modified, ambiguous, degenerate, or non-manifold topology may not be recoverable automatically.

## License

Copyright © 2026 STUDIO WAKABA

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE) for details.

---

## 日本語

**Restore Bevel Edge** は、Blenderで適用済みのベベル形状から、元の鋭いエッジを復元するためのアドオンです。

### 主な機能

- ベベル面を1面選択して復元
- ベベル片側の境界エッジ／連続エッジから復元
- ベベル片側の連続頂点から復元
- 頂点を1つだけ選択して、その位置だけ局所的に復元
- 元の2面の交線を計算してエッジを再構築
- Undo対応

### 使い方

1. メッシュを **Edit Mode** にします。
2. Face / Edge / Vertex のいずれかで対象のベベル形状を選択します。
3. 3Dビューで右クリックします。
4. **「ベベルを元のエッジに戻す」** を実行します。

周囲の面情報から元のエッジを推定するため、トポロジーが大きく変更されている場合や、元の面を特定できない形状では正しく復元できないことがあります。
