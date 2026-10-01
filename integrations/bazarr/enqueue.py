#!/usr/bin/env python3
"""Bazarr custom post-processing -> semsync queue. Installed as /data/.semsync/enqueue.py and run by
Bazarr's own python (no shell: Bazarr passes each {{variable}} as one argument):

  /lsiopy/bin/python3 /data/.semsync/enqueue.py {{episode}} {{subtitles}} {{score}}
"""
import json, os, sys, time

video, sub = sys.argv[1], sys.argv[2]
score = sys.argv[3] if len(sys.argv) > 3 else ""
queue = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue")
os.makedirs(queue, exist_ok=True)
name = f"{time.time_ns()}.job"
tmp = os.path.join(queue, name + ".tmp")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"video": video, "sub": sub, "score": score, "origin": "bazarr"}, f, ensure_ascii=False)
os.replace(tmp, os.path.join(queue, name))      # atomic: the worker never reads a half-written job
print(f"semsync: queued {os.path.basename(sub)}")
