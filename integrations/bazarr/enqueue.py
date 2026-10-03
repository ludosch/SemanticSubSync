#!/usr/bin/env python3
"""Bazarr custom post-processing -> semsync queue. Installed as /data/.semsync/enqueue.py and run by
Bazarr's own python (no shell: Bazarr passes each {{variable}} as one argument):

  /lsiopy/bin/python3 /data/.semsync/enqueue.py {{episode}} {{subtitles}} {{score}}

The job goes to $SEMSYNC_DIR/queue when SEMSYNC_DIR is set, else to the queue folder next to
this file. The score is optional (empty or missing: no score).
"""
import json, os, sys, time


def main(args):
    if len(args) < 2 or not args[0] or not args[1]:
        sys.exit("semsync: usage: enqueue.py VIDEO SUBTITLE [SCORE] (Bazarr: {{episode}} {{subtitles}} {{score}})")
    video, sub = args[0], args[1]
    score = args[2] if len(args) > 2 else ""
    base = os.environ.get("SEMSYNC_DIR") or os.path.dirname(os.path.abspath(__file__))
    queue = os.path.join(base, "queue")
    os.makedirs(queue, exist_ok=True)
    name = f"{time.time_ns()}-{os.getpid()}-bazarr.job"
    tmp = os.path.join(queue, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"video": video, "sub": sub, "score": score, "origin": "bazarr"}, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(queue, name))      # atomic: the worker never reads a half-written job
    print(f"semsync: queued {os.path.basename(sub)}")


if __name__ == "__main__":
    main(sys.argv[1:])
