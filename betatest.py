"""Beta-test harness: generate a 10x10 loop and score loop seamlessness."""
import sys
import numpy as np
import cv2
import core

VIDEO = r"c:\Users\matth\Downloads\video_Seedance2.0_flf2v_00006_.mp4"


def seam_report(seq, label):
    """Compare the wrap transition (last->first) to the typical frame-to-frame
    motion. ratio ~1.0 means the loop point is as smooth as normal playback."""
    diffs = []
    for i in range(len(seq)):
        a = seq[i].astype(np.float32)
        b = seq[(i + 1) % len(seq)].astype(np.float32)
        diffs.append(np.mean(np.abs(a - b)))
    diffs = np.array(diffs)
    wrap = diffs[-1]                 # last frame -> first frame
    body = np.median(diffs[:-1])     # typical interior transition
    ratio = wrap / body if body > 0 else float("inf")
    print(f"[{label}] median frame delta={body:6.3f}  seam delta={wrap:6.3f}  "
          f"seam/median ratio={ratio:5.2f}  max delta={diffs.max():6.3f}")
    return ratio


def main(cell=256, cols=10, rows=10, blend=4, mode="linear"):
    print(f"Decoding {VIDEO} ...")
    frames = core.decode_square_frames(VIDEO, cell)
    print(f"Decoded {len(frames)} frames @ {cell}px")

    n = cols * rows
    no_loop = core.build_sequence(frames, cols, rows, False, 0, "linear")
    looped = core.build_sequence(frames, cols, rows, True, blend, mode)

    print("\nLoop seamlessness (lower seam ratio = smoother loop):")
    seam_report(no_loop, "no-loop ")
    seam_report(looped, f"loop b={blend} {mode}")

    res = core.make_flipbook(VIDEO, cols, rows, cell, loop=True,
                             blend_frames=blend, mode=mode, frames=frames)
    out = f"output/betatest_{cols}x{rows}_{cell}px.png"
    import os
    os.makedirs("output", exist_ok=True)
    cv2.imwrite(out, res.sheet_bgr)
    print(f"\nWrote {out}  ({res.sheet_width}x{res.sheet_height})")


if __name__ == "__main__":
    kw = {}
    if len(sys.argv) > 1:
        kw["blend"] = int(sys.argv[1])
    if len(sys.argv) > 2:
        kw["mode"] = sys.argv[2]
    main(**kw)
