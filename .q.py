import sqlite3
c=sqlite3.connect(r'E:\engtutor\data\engtutor.db')
ws=['outlook','view','forecast','conserve','maintain','keep','store','protect','save','first','firstly','third','thirdly','finally','lastly','harsh','serious','intense','heavy','strong','snowy','rain','rainy','weather','ice','possibility','chance','second','expectation','future']
qs=','.join('?'*len(ws))
for r in c.execute("select word,meaning_ko,pattern,example,example_ko,usage_note from words where word in (%s) or meaning_ko like '%%전망%%'"%qs, ws):
    print(' | '.join(str(x) for x in r))
