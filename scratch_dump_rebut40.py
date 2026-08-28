import sqlite3
words = "annual contrast roll reality photograph artist conflict entire presence crowd corner gas shift net category secretary defense quick spread nuclear scale driver ball cry introduction requirement north senior photo transport map concept island reform neither football survive flight left solve".split()
c = sqlite3.connect('/workspace/data/engtutor.db')
c.row_factory = sqlite3.Row
for w in words:
    r = c.execute("select * from words where word=?", (w,)).fetchone()
    if not r:
        print("=== %s : NOT FOUND" % w)
        continue
    print("=== %s (level=%s rank=%s reviewed=%s)" % (r['word'], r['level'], r['rank'], r['reviewed']))
    for k in ('meaning_ko', 'pattern', 'example', 'example_ko', 'usage_note', 'confused_with'):
        print("  %-12s %s" % (k, r[k]))
