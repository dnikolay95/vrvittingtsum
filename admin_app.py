# -*- coding: utf-8 -*-
"""
Админка для бота: просмотр пользователей, фото и таблицы примерок.
Запуск: python admin_app.py  (порт 5002)
"""
import json
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from flask import (
    Flask,
    send_from_directory,
    render_template_string,
    request,
    redirect,
    url_for,
    session,
    jsonify,
)

import bot_db

app = Flask(__name__)
app.secret_key = os.environ.get("ADMIN_SECRET_KEY", "change-this-secret-k3y")
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "tsum")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "tsum777")

BOT_PHOTOS_DIR = Path(__file__).parent / "bot_photos"
BOT_RESULT_DIR = Path(__file__).parent / "bot_result"


def safe_path(base: Path, subpath: str) -> Path:
    p = (base / subpath).resolve()
    try:
        p.relative_to(base.resolve())
    except ValueError:
        return None
    return p if p.exists() else None


def _rel_photo(path_str: str, base: Path) -> str:
    if not path_str:
        return ""
    path_str = path_str.replace("\\", "/")
    base_str = str(base.resolve()).replace("\\", "/")
    if base_str in path_str:
        return path_str.split(base_str, 1)[-1].lstrip("/")
    return path_str.split("/")[-2] + "/" + path_str.split("/")[-1] if "/" in path_str else path_str


# ── Auth ──────────────────────────────────────────────────────────────────

@app.before_request
def require_login():
    allowed = ("login", "static")
    if request.endpoint in allowed:
        return
    if request.path.startswith("/photos/") or request.path.startswith("/results/"):
        return
    if session.get("admin_logged_in"):
        return
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("login") == ADMIN_LOGIN and request.form.get("password") == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Неверный логин или пароль"
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("login"))


# ── API (JSON) ────────────────────────────────────────────────────────────

@app.route("/api/users")
def api_users():
    users = bot_db.get_all_users_with_photos(limit=500)
    for u in users:
        u["photo_url"] = _rel_photo(u.get("last_photo_path") or "", BOT_PHOTOS_DIR)
    return jsonify(users)


@app.route("/api/tryons")
def api_tryons():
    tryons = bot_db.get_all_tryons(limit=1000)
    tryon_ids = [t["id"] for t in tryons]
    ratings_map = bot_db.get_ratings_for_tryons(tryon_ids) if tryon_ids else {}
    for t in tryons:
        t["rating"] = ratings_map.get(t["id"], {})
        t["person_photo_url"] = _rel_photo(t.get("person_photo_path") or "", BOT_PHOTOS_DIR)
        t["result_photo_url"] = _rel_photo(t.get("result_photo_path") or "", BOT_RESULT_DIR)
    return jsonify(tryons)


# ── Static files ──────────────────────────────────────────────────────────

@app.route("/photos/<path:subpath>")
def serve_bot_photo(subpath):
    p = safe_path(BOT_PHOTOS_DIR, subpath)
    if not p or not p.is_file():
        return "Not found", 404
    return send_from_directory(p.parent, p.name)


@app.route("/results/<path:subpath>")
def serve_result_photo(subpath):
    p = safe_path(BOT_RESULT_DIR, subpath)
    if not p or not p.is_file():
        return "Not found", 404
    return send_from_directory(p.parent, p.name)


# ── Pages ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(ADMIN_HTML)


# ── Templates ─────────────────────────────────────────────────────────────

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Вход — Админка</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:#f0f2f5;min-height:100vh;display:flex;align-items:center;justify-content:center}
    .card{width:380px;background:#fff;border-radius:12px;padding:32px;box-shadow:0 4px 24px rgba(0,0,0,.08)}
    h1{font-size:1.3rem;margin-bottom:20px;color:#1a1a2e}
    label{display:block;margin-top:14px;font-size:.9rem;color:#555}
    input{width:100%;padding:10px 12px;margin-top:4px;border:1px solid #ddd;border-radius:6px;font-size:.95rem;transition:border .2s}
    input:focus{outline:none;border-color:#4361ee}
    button{margin-top:20px;width:100%;padding:10px;border:none;border-radius:6px;background:#4361ee;color:#fff;font-size:1rem;cursor:pointer;transition:background .2s}
    button:hover{background:#3a56d4}
    .error{color:#e63946;margin-top:12px;font-size:.9rem}
  </style>
</head>
<body>
  <div class="card">
    <h1>Вход в админку</h1>
    <form method="post">
      <label>Логин<input type="text" name="login" autocomplete="username"></label>
      <label>Пароль<input type="password" name="password" autocomplete="current-password"></label>
      <button type="submit">Войти</button>
      {% if error %}<div class="error">{{ error }}</div>{% endif %}
    </form>
  </div>
</body>
</html>
"""


ADMIN_HTML = r"""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Админка — Виртуальная примерка</title>
  <style>
    :root{--accent:#4361ee;--accent-hover:#3a56d4;--bg:#f5f6fa;--card:#fff;--border:#e8e8ef;--text:#1a1a2e;--muted:#888;--green:#2e7d32;--red:#c62828;--orange:#e65100;--blue:#1565c0;--gold:#f9a825}
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;line-height:1.4}

    /* ─ Header ─ */
    .hdr{background:var(--card);padding:10px 20px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);position:sticky;top:0;z-index:200}
    .hdr h1{font-size:14px;font-weight:700;letter-spacing:.3px}
    .hdr a{color:var(--accent);text-decoration:none;font-size:12px}

    /* ─ Tabs ─ */
    .tabs{display:flex;background:var(--card);border-bottom:1px solid var(--border);padding:0 20px;position:sticky;top:39px;z-index:199}
    .tab-btn{padding:8px 18px;font-size:13px;cursor:pointer;border:none;background:none;border-bottom:2px solid transparent;color:var(--muted);font-weight:500;transition:.15s}
    .tab-btn:hover{color:var(--accent)}
    .tab-btn.active{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}

    /* ─ Main ─ */
    .main{padding:14px 20px;max-width:1480px;margin:0 auto}
    .pnl{display:none}.pnl.active{display:block;animation:fadeIn .2s ease}
    @keyframes fadeIn{from{opacity:0}to{opacity:1}}

    /* ─ Stats ─ */
    .stats{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
    .st{background:var(--card);padding:8px 14px;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.05);min-width:80px}
    .st b{font-size:18px;display:block}
    .st small{color:var(--muted);font-size:11px}

    /* ─ Filters ─ */
    .filters{background:var(--card);padding:8px 12px;border-radius:6px;margin-bottom:10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;box-shadow:0 1px 3px rgba(0,0,0,.04)}
    .fg{display:flex;align-items:center;gap:4px}
    .fg>span{font-size:11px;color:var(--muted);white-space:nowrap}
    .filters select,.filters input{padding:4px 8px;border:1px solid var(--border);border-radius:4px;font-size:12px;background:#fafafa;outline:none}
    .filters select:focus,.filters input:focus{border-color:var(--accent)}
    .filters input[type=text]{width:130px}
    .filters input[type=date]{width:120px;font-size:11px}
    .btn-s{padding:4px 10px;border:none;border-radius:4px;font-size:11px;cursor:pointer;transition:.15s}
    .btn-a{background:var(--accent);color:#fff}.btn-a:hover{background:var(--accent-hover)}
    .btn-r{background:#eee;color:#555}.btn-r:hover{background:#ddd}

    /* ─ Table ─ */
    .tw{background:var(--card);border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.05);overflow-x:auto}
    table{width:100%;border-collapse:collapse;table-layout:fixed}
    th,td{padding:6px 8px;text-align:left;border-bottom:1px solid #f0f0f2;overflow:hidden;text-overflow:ellipsis}
    th{background:#f8f9fb;font-size:11px;color:var(--muted);font-weight:600;white-space:nowrap;cursor:pointer;user-select:none;position:sticky;top:0;z-index:5}
    th:hover{color:var(--accent)}
    th .arr{font-size:9px;opacity:.35;margin-left:2px}
    th.sorted .arr{opacity:1;color:var(--accent)}
    td{font-size:12px;vertical-align:middle}
    tr:hover td{background:#f8faff}

    /* ─ Column widths — Users ─ */
    .u-tbl .c-id{width:40px}.u-tbl .c-tg{width:100px}.u-tbl .c-nm{width:160px}.u-tbl .c-dt{width:100px}.u-tbl .c-ph{width:70px}

    /* ─ Column widths — Tryons ─ */
    .t-tbl .c-id{width:36px}
    .t-tbl .c-dt{width:80px}
    .t-tbl .c-usr{width:120px}
    .t-tbl .c-tp{width:56px}
    .t-tbl .c-st{width:52px}
    .t-tbl .c-pho{width:80px}
    .t-tbl .c-prod{width:180px}
    .t-tbl .c-res{width:80px}
    .t-tbl .c-rat{width:90px}

    /* ─ Images ─ */
    .thumb{width:56px;height:56px;object-fit:cover;border-radius:4px;cursor:pointer;transition:transform .15s;display:block}
    .thumb:hover{transform:scale(1.06)}
    .thumb-lg{width:72px;height:72px}
    .no-img{color:#ccc;font-size:11px}

    /* ─ Badge ─ */
    .b{display:inline-block;padding:1px 6px;border-radius:8px;font-size:10px;font-weight:600;line-height:16px;white-space:nowrap}
    .b-single{background:#e8f5e9;color:var(--green)}.b-multi{background:#e3f2fd;color:var(--blue)}.b-repeat{background:#fff3e0;color:var(--orange)}
    .b-ok{background:#e8f5e9;color:var(--green)}.b-fail{background:#ffebee;color:var(--red)}

    /* ─ Stars ─ */
    .stars{color:var(--gold);font-size:12px;letter-spacing:-1px}
    .stars-empty{color:#ddd}
    .comment{font-size:11px;color:#777;margin-top:1px;max-width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

    /* ─ Product links ─ */
    .prod-link{display:block;font-size:11px;color:var(--accent);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:170px;line-height:1.4}
    .prod-link:hover{text-decoration:underline}
    .prod-brand{font-size:10px;color:var(--muted)}

    /* ─ User cell ─ */
    .usr-name{font-weight:500;font-size:12px;line-height:1.3}
    .usr-tg{font-size:10px;color:var(--muted)}

    /* ─ Lightbox ─ */
    .lb{position:fixed;inset:0;background:rgba(0,0,0,.78);z-index:1000;display:none;align-items:center;justify-content:center;cursor:zoom-out}
    .lb.show{display:flex}
    .lb img{max-width:92vw;max-height:92vh;border-radius:6px;box-shadow:0 8px 40px rgba(0,0,0,.5)}

    /* ─ Loading ─ */
    .ld{text-align:center;padding:30px;color:var(--muted)}
    .spin{display:inline-block;width:20px;height:20px;border:2px solid #e0e0e0;border-top-color:var(--accent);border-radius:50%;animation:sp .5s linear infinite;margin-bottom:4px}
    @keyframes sp{to{transform:rotate(360deg)}}
    .empty{text-align:center;padding:24px;color:#bbb;font-size:12px}

    @media(max-width:900px){.main{padding:8px}.filters{gap:6px}.st{min-width:60px;padding:6px 10px}.st b{font-size:15px}}
  </style>
</head>
<body>

<div class="hdr">
  <h1>TSUM Virtual Try-On Admin</h1>
  <a href="/logout">Выход</a>
</div>
<div class="tabs">
  <button class="tab-btn active" data-t="users">Пользователи</button>
  <button class="tab-btn" data-t="tryons">Примерки</button>
</div>

<div class="main">

<!-- ═══ Users ═══ -->
<div id="p-users" class="pnl active">
  <div class="stats" id="u-stats"></div>
  <div class="filters">
    <div class="fg"><span>Поиск</span><input type="text" id="u-q" placeholder="Имя, TG ID…"></div>
  </div>
  <div class="tw">
    <table class="u-tbl" id="u-table">
      <thead><tr>
        <th class="c-id" data-c="id"># <span class="arr">&#9650;</span></th>
        <th class="c-tg" data-c="telegram_id">TG ID <span class="arr">&#9650;</span></th>
        <th class="c-nm" data-c="first_name">Имя / Фамилия <span class="arr">&#9650;</span></th>
        <th class="c-dt" data-c="created_at">Дата <span class="arr">&#9650;</span></th>
        <th class="c-ph">Фото</th>
      </tr></thead>
      <tbody id="u-body"></tbody>
    </table>
  </div>
  <div id="u-ld" class="ld"><div class="spin"></div><br>Загрузка…</div>
</div>

<!-- ═══ Tryons ═══ -->
<div id="p-tryons" class="pnl">
  <div class="stats" id="t-stats"></div>
  <div class="filters">
    <div class="fg"><span>Тип</span>
      <select id="f-type"><option value="">все</option><option value="single">single</option><option value="multi">multi</option><option value="repeat">repeat</option></select>
    </div>
    <div class="fg"><span>Статус</span>
      <select id="f-status"><option value="">все</option><option value="ok">OK</option><option value="fail">ошибка</option></select>
    </div>
    <div class="fg"><span>Оценка</span>
      <select id="f-rating">
        <option value="">все</option><option value="with">есть</option><option value="without">нет</option>
        <option value="5">5</option><option value="4">4</option><option value="3">3</option><option value="2">2</option><option value="1">1</option>
      </select>
    </div>
    <div class="fg"><span>Пользователь</span>
      <select id="f-user"><option value="">все</option></select>
    </div>
    <div class="fg"><span>С</span><input type="date" id="f-from"></div>
    <div class="fg"><span>По</span><input type="date" id="f-to"></div>
    <div class="fg"><span>Поиск</span><input type="text" id="t-q" placeholder="Товар, бренд…"></div>
    <button class="btn-s btn-r" id="f-reset">Сброс</button>
  </div>
  <div class="tw">
    <table class="t-tbl" id="t-table">
      <thead><tr>
        <th class="c-id" data-c="id"># <span class="arr">&#9650;</span></th>
        <th class="c-dt" data-c="created_at">Дата <span class="arr">&#9650;</span></th>
        <th class="c-usr" data-c="first_name">Пользователь <span class="arr">&#9650;</span></th>
        <th class="c-tp" data-c="tryon_type">Тип <span class="arr">&#9650;</span></th>
        <th class="c-st" data-c="status">Статус <span class="arr">&#9650;</span></th>
        <th class="c-pho">Фото</th>
        <th class="c-prod">Товары</th>
        <th class="c-res">Результат</th>
        <th class="c-rat" data-c="rating_stars">Оценка <span class="arr">&#9650;</span></th>
      </tr></thead>
      <tbody id="t-body"></tbody>
    </table>
  </div>
  <div id="t-ld" class="ld"><div class="spin"></div><br>Загрузка…</div>
</div>

</div><!-- /main -->

<div class="lb" id="lb"><img src="" alt=""></div>

<script>
(function(){
  /* ── state ── */
  let U=[],T=[],uCol='id',uAsc=false,tCol='id',tAsc=false;

  /* ── tabs ── */
  document.querySelectorAll('.tab-btn').forEach(b=>{
    b.onclick=()=>{
      document.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));
      document.querySelectorAll('.pnl').forEach(x=>x.classList.remove('active'));
      b.classList.add('active');
      document.getElementById('p-'+b.dataset.t).classList.add('active');
    };
  });

  /* ── lightbox ── */
  const lb=document.getElementById('lb'),lbi=lb.querySelector('img');
  lb.onclick=()=>lb.classList.remove('show');
  window.openLb=src=>{lbi.src=src;lb.classList.add('show');};

  /* ── helpers ── */
  const esc=s=>{const d=document.createElement('div');d.textContent=s||'';return d.innerHTML;};
  const fmtDt=s=>{
    if(!s) return '—';
    try{const d=new Date(s);const dd=String(d.getDate()).padStart(2,'0'),mm=String(d.getMonth()+1).padStart(2,'0'),hh=String(d.getHours()).padStart(2,'0'),mi=String(d.getMinutes()).padStart(2,'0');return dd+'.'+mm+'.'+String(d.getFullYear()).slice(2)+'<br>'+hh+':'+mi;}
    catch(e){return s;}
  };
  const fmtDtFlat=s=>{
    if(!s) return '—';
    try{const d=new Date(s);const dd=String(d.getDate()).padStart(2,'0'),mm=String(d.getMonth()+1).padStart(2,'0'),hh=String(d.getHours()).padStart(2,'0'),mi=String(d.getMinutes()).padStart(2,'0');return dd+'.'+mm+'.'+String(d.getFullYear()).slice(2)+' '+hh+':'+mi;}
    catch(e){return s;}
  };
  const dateOnly=s=>{try{return new Date(s).toISOString().slice(0,10);}catch(e){return '';}};
  const badge=(txt,cls)=>'<span class="b '+cls+'">'+esc(txt)+'</span>';
  const typeBadge=t=>badge(t,{single:'b-single',multi:'b-multi',repeat:'b-repeat'}[t]||'');
  const statusBadge=t=>t.result_photo_path?badge('OK','b-ok'):badge('Ошибка','b-fail');
  const stars=r=>{
    if(!r||!r.stars) return '<span class="stars-empty">—</span>';
    let h='<span class="stars">'+'★'.repeat(r.stars)+'☆'.repeat(5-r.stars)+'</span>';
    if(r.comment) h+='<div class="comment" title="'+esc(r.comment)+'">'+esc(r.comment)+'</div>';
    return h;
  };
  const img=(url,pfx,big)=>{
    if(!url) return '<span class="no-img">—</span>';
    const f=pfx+url;
    return '<img class="thumb'+(big?' thumb-lg':'')+'" src="'+f+'" onclick="openLb(\''+f.replace(/'/g,"\\'")+'\')" onerror="this.outerHTML=\'<span class=no-img>—</span>\'" loading="lazy">';
  };

  /* ── sort ── */
  const srt=(d,col,asc)=>[...d].sort((a,b)=>{
    let va=a[col],vb=b[col];
    if(col==='rating_stars'){va=(a.rating||{}).stars||0;vb=(b.rating||{}).stars||0;}
    if(col==='status'){va=a.result_photo_path?1:0;vb=b.result_photo_path?1:0;}
    if(va==null)va='';if(vb==null)vb='';
    if(typeof va==='number'&&typeof vb==='number') return asc?va-vb:vb-va;
    return asc?String(va).localeCompare(String(vb),'ru'):String(vb).localeCompare(String(va),'ru');
  });

  /* ── build user dropdown for tryons filter ── */
  function buildUserSelect(){
    const sel=document.getElementById('f-user'),seen={};
    let opts='<option value="">все</option>';
    T.forEach(t=>{
      const uid=t.user_id;
      if(!seen[uid]){
        seen[uid]=true;
        const nm=((t.first_name||'')+' '+(t.last_name||'')).trim()||'id:'+uid;
        opts+='<option value="'+uid+'">'+esc(nm)+'</option>';
      }
    });
    sel.innerHTML=opts;
  }

  /* ── render users ── */
  function renderU(){
    const q=document.getElementById('u-q').value.toLowerCase();
    let d=U;
    if(q) d=d.filter(u=>(u.first_name||'').toLowerCase().includes(q)||(u.last_name||'').toLowerCase().includes(q)||String(u.telegram_id).includes(q));
    d=srt(d,uCol,uAsc);
    document.getElementById('u-stats').innerHTML=
      '<div class="st"><b>'+U.length+'</b><small>Всего</small></div>'+
      '<div class="st"><b>'+d.length+'</b><small>Показано</small></div>';
    const tb=document.getElementById('u-body');
    if(!d.length){tb.innerHTML='<tr><td colspan="5" class="empty">Нет данных</td></tr>';return;}
    tb.innerHTML=d.map(u=>
      '<tr><td>'+u.id+'</td>'+
      '<td>'+u.telegram_id+'</td>'+
      '<td><div class="usr-name">'+esc(u.first_name)+' '+esc(u.last_name)+'</div></td>'+
      '<td>'+fmtDtFlat(u.created_at)+'</td>'+
      '<td>'+img(u.photo_url,'/photos/',true)+'</td></tr>'
    ).join('');
  }

  /* ── render tryons ── */
  function renderT(){
    const fType=document.getElementById('f-type').value;
    const fSt=document.getElementById('f-status').value;
    const fRat=document.getElementById('f-rating').value;
    const fUsr=document.getElementById('f-user').value;
    const fFrom=document.getElementById('f-from').value;
    const fTo=document.getElementById('f-to').value;
    const q=document.getElementById('t-q').value.toLowerCase();
    let d=T;
    if(fType) d=d.filter(t=>t.tryon_type===fType);
    if(fSt==='ok') d=d.filter(t=>t.result_photo_path);
    if(fSt==='fail') d=d.filter(t=>!t.result_photo_path);
    if(fRat==='with') d=d.filter(t=>t.rating&&t.rating.stars);
    else if(fRat==='without') d=d.filter(t=>!t.rating||!t.rating.stars);
    else if(fRat) d=d.filter(t=>t.rating&&t.rating.stars===parseInt(fRat));
    if(fUsr) d=d.filter(t=>String(t.user_id)===fUsr);
    if(fFrom) d=d.filter(t=>dateOnly(t.created_at)>=fFrom);
    if(fTo) d=d.filter(t=>dateOnly(t.created_at)<=fTo);
    if(q) d=d.filter(t=>{
      const hay=((t.product_titles||[]).join(' ')+' '+(t.product_brands||[]).join(' ')+' '+(t.first_name||'')+' '+(t.last_name||'')+' '+t.telegram_id).toLowerCase();
      return hay.includes(q);
    });
    d=srt(d,tCol,tAsc);

    const all=T.length,ok=T.filter(t=>t.result_photo_path).length,fail=all-ok,rated=T.filter(t=>t.rating&&t.rating.stars).length;
    document.getElementById('t-stats').innerHTML=
      '<div class="st"><b>'+all+'</b><small>Всего</small></div>'+
      '<div class="st"><b style="color:var(--green)">'+ok+'</b><small>Успешных</small></div>'+
      '<div class="st"><b style="color:var(--red)">'+fail+'</b><small>Неудачных</small></div>'+
      '<div class="st"><b style="color:var(--gold)">'+rated+'</b><small>С оценкой</small></div>'+
      '<div class="st"><b>'+d.length+'</b><small>Показано</small></div>';

    const tb=document.getElementById('t-body');
    if(!d.length){tb.innerHTML='<tr><td colspan="9" class="empty">Нет данных</td></tr>';return;}
    tb.innerHTML=d.map(t=>{
      const prods=(t.product_links||[]).map((l,i)=>{
        const title=esc((t.product_titles||[])[i]||'Товар');
        const brand=esc((t.product_brands||[])[i]||'');
        return '<a class="prod-link" href="'+esc(l)+'" target="_blank" title="'+title+'">'+title+'</a>'+(brand?'<span class="prod-brand">'+brand+'</span>':'');
      }).join('');
      const prev=t.previous_tryon_id?'<span style="font-size:10px;color:var(--muted)"> #'+t.previous_tryon_id+'</span>':'';
      return '<tr>'+
        '<td>'+t.id+'</td>'+
        '<td>'+fmtDt(t.created_at)+'</td>'+
        '<td><div class="usr-name">'+esc(t.first_name)+'<br>'+esc(t.last_name)+'</div><div class="usr-tg">'+t.telegram_id+'</div></td>'+
        '<td>'+typeBadge(t.tryon_type)+prev+'</td>'+
        '<td>'+statusBadge(t)+'</td>'+
        '<td>'+img(t.person_photo_url,'/photos/',true)+'</td>'+
        '<td>'+prods+'</td>'+
        '<td>'+img(t.result_photo_url,'/results/',true)+'</td>'+
        '<td>'+stars(t.rating)+'</td>'+
        '</tr>';
    }).join('');
  }

  /* ── sorting ── */
  document.querySelectorAll('#u-table th[data-c]').forEach(th=>{
    th.onclick=()=>{
      const c=th.dataset.c;
      if(uCol===c)uAsc=!uAsc;else{uCol=c;uAsc=true;}
      document.querySelectorAll('#u-table th').forEach(h=>h.classList.remove('sorted'));
      th.classList.add('sorted');th.querySelector('.arr').innerHTML=uAsc?'&#9650;':'&#9660;';
      renderU();
    };
  });
  document.querySelectorAll('#t-table th[data-c]').forEach(th=>{
    th.onclick=()=>{
      const c=th.dataset.c;
      if(tCol===c)tAsc=!tAsc;else{tCol=c;tAsc=true;}
      document.querySelectorAll('#t-table th').forEach(h=>h.classList.remove('sorted'));
      th.classList.add('sorted');th.querySelector('.arr').innerHTML=tAsc?'&#9650;':'&#9660;';
      renderT();
    };
  });

  /* ── filter listeners ── */
  document.getElementById('u-q').addEventListener('input',renderU);
  ['f-type','f-status','f-rating','f-user','f-from','f-to'].forEach(id=>document.getElementById(id).addEventListener('change',renderT));
  document.getElementById('t-q').addEventListener('input',renderT);
  document.getElementById('f-reset').onclick=()=>{
    ['f-type','f-status','f-rating','f-user','f-from','f-to'].forEach(id=>document.getElementById(id).value='');
    document.getElementById('t-q').value='';
    renderT();
  };

  /* ── load ── */
  async function load(){
    try{
      const [ur,tr]=await Promise.all([fetch('/api/users'),fetch('/api/tryons')]);
      U=await ur.json(); T=await tr.json();
    }catch(e){console.error(e);}
    document.getElementById('u-ld').style.display='none';
    document.getElementById('t-ld').style.display='none';
    buildUserSelect();
    renderU(); renderT();
  }
  load();
})();
</script>
</body>
</html>
"""


@app.context_processor
def inject():
    return {"backslash": "\\"}


if __name__ == "__main__":
    bot_db.init_db()
    app.run(host="0.0.0.0", port=5002, debug=True)
