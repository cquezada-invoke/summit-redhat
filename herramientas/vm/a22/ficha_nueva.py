def ficha_test(br):
    c = br.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
    c.add_cookies([{"name": D["cookie_nombre"], "value": D["ck_nucleo"], "url": D["origen"]}])
    estilos_ag = {}
    for nom, eid in D["fichas"]:
        if not eid:
            print("FICHA", nom, "sin equipo", flush=True); continue
        pg = c.new_page(); errs = []
        pg.on("console", lambda m: errs.append(m.text[:120]) if m.type == "error" else None)
        pg.on("pageerror", lambda e_: errs.append("pageerror " + str(e_)[:120]))
        t0 = time.time(); r = pg.goto(D["base"] + f"/lab/inventario/{eid}", wait_until="domcontentloaded"); fria = round((time.time() - t0) * 1000)
        try: pg.wait_for_load_state("networkidle", timeout=8000)
        except Exception: pass
        t0 = time.time(); pg.reload(wait_until="domcontentloaded"); cal = round((time.time() - t0) * 1000)
        try: pg.wait_for_load_state("networkidle", timeout=8000)
        except Exception: pass
        pg.wait_for_timeout(600)
        info = pg.evaluate("""(()=>{const so=document.querySelector('.cajon-p[data-so]');
          const enB=[...document.querySelectorAll('[data-energia]')].map(b=>b.dataset.energia+(b.disabled?'(deshab)':'')+(b.classList.contains('bloq-etapa')?'(bloq)':''));
          const skBtns=[...document.querySelectorAll('main button,main a')].filter(b=>/skills/i.test(b.getAttribute('aria-label')||'')&&!b.closest('#sk-cajon')&&!b.classList.contains('sk-mini')&&b.offsetParent);
          const ag=document.querySelector('#agentes .ag-mas');const cs=ag?getComputedStyle(ag):null;
          return {energia:enB, so_salud:!!(so&&so.querySelector('.salud-res')), botones_skills:skBtns.map(b=>b.id||b.className), sk_btn_izq:!!document.getElementById('sk-btn'),
            hb_bloq:!!(document.getElementById('hb-btn')||{classList:{contains:()=>false}}).classList.contains('bloq-etapa'),
            al_bloq:!!(document.querySelector('.al-titulo .al-btn')||{classList:{contains:()=>false}}).classList.contains('bloq-etapa'),
            ag:ag?{cls:ag.className.replace(' bloq-etapa',''),bloq:ag.classList.contains('bloq-etapa'),glosa:ag.dataset.bloqGlosa||'',font:cs.fontSize,pad:cs.padding,h:Math.round(ag.getBoundingClientRect().height),w:Math.round(ag.getBoundingClientRect().width)}:null,
            fw:[...document.querySelectorAll('#eq-firmware dd > div')].map(d=>d.textContent.trim().slice(0,60)),
            fw_abajo:[...document.querySelectorAll('#hardware h3')].some(h=>/^Firmware/.test(h.textContent.trim())),
            ev_bmc:(()=>{const x=document.getElementById('hw-eventos-bmc');return x?(x.classList.contains('bloq-cont')||!!x.closest('.bloq-cont')?'bloqueado: '+((x.querySelector('.bloq-glosa')||{}).textContent||''):'VISIBLE sin bloqueo'):'no está'})(),
            cluster:[...document.querySelectorAll('a[href*="cluster-vmware"]')].map(a=>a.textContent.trim()).slice(0,2),
            hscroll: document.documentElement.scrollWidth>document.documentElement.clientWidth}})()""")
        if info.get("ag"): estilos_ag[nom] = info["ag"]
        der = lambda sel: pg.evaluate("(s)=>{const x=document.querySelector(s);if(!x)return null;const r=x.getBoundingClientRect();return {visible:r.width>0&&r.height>0,derecha:Math.abs(innerWidth-r.right)<3,alto:Math.abs(innerHeight-r.height)<3||r.height>=innerHeight-2,ancho:Math.round(r.width)}}", sel)
        sk = {}
        try:
            pg.click("#hb-btn"); pg.wait_for_timeout(700)
            sk = der("#sk-cajon") or {}
            sk.update(pg.evaluate("""(()=>{const c=document.getElementById('sk-cajon');const gr=[...c.querySelectorAll('.sk-grupo')].filter(g=>!g.hidden).map(g=>g.querySelector('summary').childNodes[0].textContent.trim());
              const it=[...c.querySelectorAll('.sk-item')].filter(i=>!i.hidden&&!i.closest('[hidden]'));
              const ej=[...c.querySelectorAll('button')].filter(b=>/Ejecutar|Aplicar|Actualizar|Evaluar|Iniciar|Detener|Reiniciar/.test(b.textContent)&&!b.closest('[hidden]'));
              const inv=c.querySelector('.sk-inv-ir');
              return {grupos:gr, items:it.length, con_desc:it.filter(i=>(i.querySelector('.sk-que')||{}).textContent).length, ejecutar:ej.length, ejecutar_bloq:ej.filter(b=>b.classList.contains('bloq-etapa')||b.disabled).length,
                inventariar_activo:!!(inv&&!inv.disabled&&!inv.classList.contains('bloq-etapa')), cerrar:!!c.querySelector('.cajon-x')}})()"""))
            pg.click("#sk-cajon .cajon-x"); pg.wait_for_timeout(400)
            sk["cierra"] = pg.evaluate("document.getElementById('sk-cajon').hidden||!document.getElementById('sk-cajon').classList.contains('abierto')")
        except Exception as x: sk["error"] = str(x)[:120]
        al = {}
        try:
            pg.click(".al-titulo .al-btn"); pg.wait_for_timeout(600)
            al = der("dialog.al-dlg[open]") or {"abre": False}
            al.update(pg.evaluate("""(()=>{const d=document.querySelector('dialog.al-dlg[open]');if(!d)return {};const ctr=[...d.querySelectorAll('button,input:not([type=hidden]),select,a[href]')].filter(b=>!b.matches('.al-x'));
              return {acciones:ctr.length, acciones_bloq:ctr.filter(b=>b.classList.contains('bloq-etapa')||b.disabled).length, glosa:((d.querySelector('.bloq-glosa-cont')||{}).textContent||'').trim(), filas:d.querySelectorAll('tr').length}})()"""))
            pg.click("dialog.al-dlg[open] .al-x"); pg.wait_for_timeout(300)
            al["cierra"] = not pg.evaluate("!!document.querySelector('dialog.al-dlg[open]')")
        except Exception as x: al["error"] = str(x)[:120]
        lv = ""
        if nom in D.get("con_logs", []):
            try:
                rutas = pg.evaluate("[...document.querySelectorAll('.cajon-p[data-so] .log-ver')].map(a=>[a.dataset.log,a.dataset.logN])")
                pref = [r_ for r_ in rutas if r_[0] in ("/var/log/messages", "journal", "evento:System")] or rutas[:1]
                for ru, nm in pref[:1]:
                    pg.evaluate("([r,n])=>fxVerLog(r,n)", [ru, nm]); t0 = time.time()
                    pg.wait_for_function("(()=>{const t=document.querySelector('#log-dlg .lg-estado').textContent;return /línea|No se|AAP no|error|falló|bloquead|Falta|perfil/i.test(t)&&!/Pidiendo|leyendo/i.test(t)})()", timeout=200000)
                    st = pg.evaluate("[document.querySelector('#log-dlg').open, document.querySelector('#log-dlg .lg-estado').textContent.slice(0,120), document.querySelector('#log-dlg .lg-cuerpo').textContent.split('\\n').length]")
                    lv += f" [{ru}: {round(time.time()-t0)} s · abierto {st[0]} · «{st[1]}» · filas {st[2]} · pestañas {len(c.pages)}]"
                    pg.evaluate("document.getElementById('log-dlg').close()")
            except Exception as x: lv += " [visor: " + str(x)[:120] + "]"
        fis = {}
        if nom.startswith("física"):
            fis = pg.evaluate("""(()=>{const c=document.getElementById('componentes');const n=c?[...c.querySelectorAll('.comp b')].map(b=>b.textContent.trim()).filter(Boolean):[];
              const h=document.getElementById('hardware');return {plataformas:n.slice(0,4), bmc_ssh:!!(h&&h.querySelector('.bmc-ssh'))}})()""")
            if fis.get("bmc_ssh") and nom == "física 97":
                with c.expect_page() as np_:
                    pg.click("#hardware .bmc-ssh")
                p2 = np_.value
                try:
                    p2.wait_for_load_state("domcontentloaded", timeout=30000); p2.wait_for_timeout(9000)
                    fis["consola_bmc"] = p2.evaluate("({url: location.pathname.slice(-30), canvas: document.querySelectorAll('canvas').length, error: ((document.querySelector('.notification, .error, .client-status-modal')||{}).textContent||'').trim().slice(0,80)})")
                except Exception as x_: fis["consola_bmc"] = "error " + str(x_)[:100]
                p2.close()
            if info.get("cluster"):
                p3 = c.new_page(); p3.goto(D["base"] + "/lab/inventario/cluster-vmware/CL-PROD-01", wait_until="domcontentloaded")
                fis["vista_cluster"] = p3.evaluate("({hosts:[...document.querySelectorAll('#cl-hosts tr a')].map(a=>a.textContent.trim()), vms:document.querySelectorAll('#cl-vms tr').length})")
                p3.close()
        print("FICHA", nom, r.status if r else 0, f"fría {fria} ms caliente {cal} ms", json.dumps(info, ensure_ascii=False), "· SKILLS", json.dumps(sk, ensure_ascii=False), "· ALERTAS", json.dumps(al, ensure_ascii=False), "· FIS", json.dumps(fis, ensure_ascii=False), "· LOGS", lv, "· errores:", errs[:3], flush=True)
        pg.close()
    vm_ = [v for k, v in estilos_ag.items() if not k.startswith("física")]; fi_ = [v for k, v in estilos_ag.items() if k.startswith("física")]
    if vm_ and fi_:
        cmp_ = {k: (vm_[0][k], fi_[0][k]) for k in ("cls", "font", "pad", "h", "w", "bloq")}
        print("AGENTES virtual vs física", json.dumps(cmp_, ensure_ascii=False), "IGUALES" if all(a == b for a, b in cmp_.values()) else "DISTINTOS", flush=True)
    c.close()
