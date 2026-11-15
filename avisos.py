"""JS Capital - Avisos. Lee velas de Kraken, aplica tus reglas y avisa por Telegram.
No opera: solo avisa."""
import os, sys, json, time, urllib.request, urllib.parse

PARES = {"BTC": "XBTUSD", "ETH": "ETHUSD"}
RIESGO_USD, SWING, MIN_STOP_PCT, RR = 30.0, 10, 0.3, 2.0

def velas(par, minutos):
    url = f"https://api.kraken.com/0/public/OHLC?pair={par}&interval={minutos}"
    with urllib.request.urlopen(url, timeout=20) as r:
        d = json.load(r)
    if d.get("error"):
        raise RuntimeError(d["error"])
    filas = next(v for k, v in d["result"].items() if k != "last")
    # [t, open, high, low, close, vwap, volume, count]
    out = [dict(t=int(f[0]), h=float(f[2]), l=float(f[3]), c=float(f[4]), v=float(f[6])) for f in filas]
    return out[:-1]  # quita la vela abierta: solo velas cerradas

def ema(x, n):
    k, e, out = 2 / (n + 1), x[0], []
    for v in x:
        e = v * k + e * (1 - k)
        out.append(e)
    return out

def rsi(c, n=14):  # RSI de Wilder
    g = [0.0] + [max(c[i] - c[i-1], 0) for i in range(1, len(c))]
    p = [0.0] + [max(c[i-1] - c[i], 0) for i in range(1, len(c))]
    ag, ap, out = sum(g[1:n+1]) / n, sum(p[1:n+1]) / n, [None] * n
    out.append(100 - 100 / (1 + ag / ap) if ap else 100)
    for i in range(n + 1, len(c)):
        ag, ap = (ag * (n-1) + g[i]) / n, (ap * (n-1) + p[i]) / n
        out.append(100 - 100 / (1 + ag / ap) if ap else 100)
    return out

def senal_15m(v):
    c = [x["c"] for x in v]
    e200 = ema(c, 200)
    macd = [a - b for a, b in zip(ema(c, 12), ema(c, 26))]
    sig = ema(macd, 9)
    i = len(c) - 1
    cruza_arriba = macd[i-1] <= sig[i-1] and macd[i] > sig[i]
    cruza_abajo = macd[i-1] >= sig[i-1] and macd[i] < sig[i]
    px = c[i]
    if c[i] > e200[i] and cruza_arriba and macd[i] < 0:
        lado = "LARGO"
    elif c[i] < e200[i] and cruza_abajo and macd[i] > 0:
        lado = "CORTO"
    else:
        return None
    if lado == "LARGO":
        dist = max(px - min(x["l"] for x in v[-SWING:]), px * MIN_STOP_PCT / 100)
        stop, obj = px - dist, px + RR * dist
    else:
        dist = max(max(x["h"] for x in v[-SWING:]) - px, px * MIN_STOP_PCT / 100)
        stop, obj = px + dist, px - RR * dist
    return (f"{lado} (15m MACD+EMA200)\nEntrada ~{px:,.2f}\nStop {stop:,.2f}\n"
            f"Objetivo {obj:,.2f}\nTamano ~{RIESGO_USD / dist:.4f} unidades (riesgo {RIESGO_USD:.0f} USD)")

def vbsm(v, largo=25):
    """VBSM de Siyeon/2tm: PVI+NVI (linea azul) contra su media (roja).
    Devuelve d = azul - roja. d > 0 = verde (alcista), d < 0 = rojo (bajista)."""
    c = [x["c"] for x in v]
    vol = [x["v"] for x in v]
    n1, n2, a, b = [0.0], [0.0], 0.0, 0.0
    for i in range(1, len(c)):
        roc = 100 * (c[i] - c[i-1]) / c[i-1]
        if vol[i] < vol[i-1]:
            a += roc
        if vol[i] > vol[i-1]:
            b += roc
        n1.append(a); n2.append(b)
    d = [None] * (largo - 1)
    for i in range(largo - 1, len(c)):
        d.append((n1[i] - sum(n1[i-largo+1:i+1]) / largo) + (n2[i] - sum(n2[i-largo+1:i+1]) / largo))
    return d

MARGEN = 2  # velas de margen entre el cruce del RSI y el cambio de color del VBSM

def senal_rsi_vbsm_1h(v):
    c = [x["c"] for x in v]
    r = rsi(c)
    m = [None] * 27 + [sum(r[i-13:i+1]) / 14 for i in range(27, len(r))]
    d = vbsm(v)
    n = len(c)
    ini = max(27, 25) + 1
    rs_up, rs_dn, vb_up, vb_dn = [False] * n, [False] * n, [False] * n, [False] * n
    for i in range(ini, n):
        rs_up[i] = r[i-1] <= m[i-1] and r[i] > m[i]
        rs_dn[i] = r[i-1] >= m[i-1] and r[i] < m[i]
        vb_up[i] = d[i-1] <= 0 < d[i]
        vb_dn[i] = d[i-1] >= 0 > d[i]
    i = n - 1
    ventana = range(max(ini, i - MARGEN), i + 1)
    if (rs_up[i] and any(vb_up[j] for j in ventana)) or (vb_up[i] and any(rs_up[j] for j in ventana)):
        return (f"AVISO 2 LARGO (1h): el RSI cruza su media al alza y el VBSM se pone verde.\n"
                f"Precio ~{c[i]:,.2f}. Stop y tamano: decidelos tu antes de entrar.")
    if (rs_dn[i] and any(vb_dn[j] for j in ventana)) or (vb_dn[i] and any(rs_dn[j] for j in ventana)):
        return (f"AVISO 2 CORTO (1h): el RSI cruza su media a la baja y el VBSM se pone rojo.\n"
                f"Precio ~{c[i]:,.2f}. Stop y tamano: decidelos tu antes de entrar.")
    return None

def telegram(texto):
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[sin Telegram configurado]\n" + texto); return
    data = urllib.parse.urlencode({"chat_id": chat, "text": texto}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=20)

def main():
    if os.environ.get("PRUEBA"):
        telegram("Prueba de JS Avisos: si lees esto, el bot funciona."); return
    ahora = time.time()
    for nombre, par in PARES.items():
        for minutos, fn in ((15, senal_15m), (60, senal_rsi_vbsm_1h)):
            try:
                v = velas(par, minutos)
                # solo si la vela se acaba de cerrar (evita repetir avisos)
                if ahora - (v[-1]["t"] + minutos * 60) > 12 * 60:
                    continue
                msg = fn(v)
                if msg:
                    telegram(f"{nombre}: {msg}")
            except Exception as e:
                print(nombre, minutos, "error:", e)

if __name__ == "__main__":
    main()
