import sys
s = open(sys.argv[1]).read(); nueva = open(sys.argv[2]).read()
i = s.index("def ficha_test(br):"); k = s.index("from playwright.sync_api import sync_playwright")
s = s[:i] + nueva + s[k:]
import re
s = re.sub(r"SHA=[0-9a-f]{8}", "SHA=7ca654f2", s, count=1)
old = '''          ("física 97", 97 if q1("SELECT 1 x FROM equipos WHERE id=97 AND cliente_id=?", cid) else None),'''
assert old in s
s = s.replace(old, old + '''
          ("física 93", 93 if q1("SELECT 1 x FROM equipos WHERE id=93 AND cliente_id=?", cid) else None),
          ("física 86", 86 if q1("SELECT 1 x FROM equipos WHERE id=86 AND cliente_id=?", cid) else None),''')
s = s.replace('"fichas": fichas, ', '"fichas": fichas, "con_logs": ["RHEL virtual srv-e1prueba-01", "Windows srv-windows-iis-01"], ')
assert '"con_logs"' in s
open(sys.argv[3], "w").write(s)
