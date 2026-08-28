import sqlite3, json, os
c=sqlite3.connect(os.environ.get('DB_PATH','./data/engtutor.db'))
ws=['unfortunately','brief','bird','demonstrate','contribution','apparently','novel','union','burn','trend','pleasure','suggestion','critical','mostly','pop','essential','desire','currently','topic','beach','attract','flower','crisis','settle','boat','aid','twice','fresh','delay','safety','engineer','quiet']
cols=[r[1] for r in c.execute('pragma table_info(words)')]
print('COLS', cols)
sel=['word','level','meaning_ko','example','example_ko','usage_note','confused_with','pattern']
extra=[x for x in ('track','reviewed','rank','topic') if x in cols]
sel=sel+extra
q='select %s from words where word in (%s) order by word' % (','.join(sel), ','.join('?'*len(ws)))
for r in c.execute(q,ws):
    print(json.dumps(dict(zip(sel,r)),ensure_ascii=False))
