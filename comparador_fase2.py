#!/usr/bin/env python3
"""TDS115 proyecto 9: condiciones acordadas en Fase 1.
Uso: python comparador_fase2.py [--demo] [--csv ARCHIVO.csv] [--out CARPETA]
Por defecto consulta Open-Meteo ERA5 real; sin acceso red falla claramente.
--demo usa una serie sintetica EXCLUSIVAMENTE para probar la implementacion.
"""
import argparse
import json
from pathlib import Path
import urllib.parse
import urllib.request
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import signal

N = 336
FS_HOUR = 1.0
SIGMA = 0.6
SEED = 115
START = '2026-10-01'
END = '2026-10-14'
# Diseño FIR añadido en fase 2: frecuencia de corte 0.125 ciclos/h
# (periodo de 8 horas), valor que no estaba fijado en fase 1.
FC_FIR = 0.125

def get_data(demo=False, csv_path=None):
    if demo:
        rng = np.random.default_rng(2026)
        hours = np.arange(N)
        # ejemplo sintético ilustrativo (NO datos de Open-Meteo)
        s = 26 + 3.2 * np.sin(2*np.pi*(hours-7)/24) + 0.55*np.sin(2*np.pi*hours/(24*5))
        s += signal.lfilter([0.04], [1, -0.94], rng.normal(size=N))
        t = pd.date_range('2026-10-01', periods=N, freq='h', tz='America/El_Salvador')
        return t, s, 'DEMOSTRACIÓN SINTÉTICA, NO Open-Meteo'
    if csv_path:
        df = pd.read_csv(csv_path)
        if not {'time','temperature_2m'}.issubset(df.columns):
            raise ValueError('CSV debe tener las columnas time y temperature_2m')
        t = pd.to_datetime(df['time'])
        s = pd.to_numeric(df['temperature_2m'], errors='raise').to_numpy(dtype=float)
        source = f'DATOS CSV: {csv_path}'
    else:
        params = dict(latitude=13.6929, longitude=-89.2182, start_date=START,
                      end_date=END, hourly='temperature_2m', timezone='America/El_Salvador',
                      models='era5')
        url = 'https://archive-api.open-meteo.com/v1/archive?' + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=25) as response:
                payload = json.load(response)
        except Exception as exc:
            raise RuntimeError('No se obtuvieron datos REALES de Open-Meteo. Conecte a internet, pase --csv o utilice --demo para simulacion ilustrativa.') from exc
        if payload.get('error'):
            raise RuntimeError(str(payload.get('reason', 'Error de Open-Meteo')))
        t = pd.to_datetime(payload['hourly']['time'])
        s = np.array(payload['hourly']['temperature_2m'], dtype=float)
        source = f'REAL Open-Meteo ERA5: {url}'
    if len(s) != N or not np.all(np.isfinite(s)):
        raise ValueError(f'Se esperan 336 valores validos; recibidos {len(s)}')
    tt = pd.DatetimeIndex(t)
    dt = pd.Series(tt).diff().dropna()
    if not (dt == pd.Timedelta(hours=1)).all():
        raise ValueError('Los datos no son estrictamente horarios y consecutivos')
    if tt[0].date().isoformat()!=START or tt[-1].date().isoformat()!=END:
        raise ValueError('El rango temporal no coincide con 1 al 14 de octubre de 2026')
    return t, s, source

def filter_signal(b, a, x):
    # Inicialización en equilibrio para evitar caída inicial artificial.
    zi = signal.lfilter_zi(b, a)*x[0]
    y, _ = signal.lfilter(b, a, x, zi=zi)
    return y

def step_delay(b, a, step_index=10):
    u = np.full(45,25.0)
    u[step_index:] = 28.0
    y = filter_signal(b,a,u)
    ix = np.flatnonzero((np.arange(len(y))>=step_index) & (y>=26.5))
    return int(ix[0]-step_index) if len(ix) else None, u, y

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='usa datos SINTETICOS, no reales')
    parser.add_argument('--csv', help='CSV local con time,temperature_2m')
    parser.add_argument('--out', default='resultados_fase2')
    args=parser.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    t,s,source = get_data(args.demo,args.csv)
    rng=np.random.default_rng(SEED)
    w=rng.normal(0,SIGMA,N)
    x=s+w
    # Cuantificación virtual documentada, sin alterar la comparación principal.
    paso_q=50/4095
    s_12bits=np.clip(np.round(s/paso_q)*paso_q,0,50)
    b_ma=np.ones(5)/5
    a_ma=np.array([1.])
    b_iir=np.array([0.35]);a_iir=np.array([1.,-0.65])
    # FIR pasabajos: fs = 1 muestra/h; fc = 0.125 ciclos/h
    b_fir=signal.firwin(7, cutoff=FC_FIR, fs=FS_HOUR, window='hamming',pass_zero='lowpass')
    a_fir=np.array([1.])
    filters={'Media móvil M=5': (b_ma,a_ma), 'IIR α=0.35': (b_iir,a_iir), 'FIR 7 Hamming': (b_fir,a_fir)}
    # Ignore 20 muestras iniciales de transitorio al comparar RMS de ruido.
    trim=20
    results=[]
    curves={}
    step_curves={}
    for name,(b,a) in filters.items():
        ys=filter_signal(b,a,s)
        yx=filter_signal(b,a,x)
        residual=(yx-ys)[trim:]
        baseline=w[trim:]
        rms_r=float(np.sqrt(np.mean(residual**2)))
        rms_w=float(np.sqrt(np.mean(baseline**2)))
        red=100*(1-rms_r/rms_w)
        # theoretical WGN noise gain: MA/FIR sum of squares; IIR infinite impulse response
        gain=(np.sqrt(0.35/(2-0.35)) if 'IIR' in name else np.sqrt(np.sum(b*b)))
        delay,step_u,step_y=step_delay(b,a)
        results.append({'Filtro':name,'RMS ruido entrada (°C)':rms_w,'RMS residual (°C)':rms_r,
                        'Reducción RMS (%)':red,'Reducción teórica RMS (%)':100*(1-gain),
                        'Retardo escalón 50% (h)':delay})
        curves[name]=yx
        step_curves[name]=step_y
    results_df=pd.DataFrame(results)
    results_df.to_csv(out/'tabla_resultados.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame({'time':t,'temperatura_referencia':s,'temperatura_cuantizada_virtual_12bits':s_12bits,'ruido_simulado':w,'entrada_ruidosa':x,
                  **{name:val for name,val in curves.items()}}).to_csv(out/'senales.csv',index=False,encoding='utf-8-sig')
    (out/'fuente.txt').write_text(source+'\nLa cuantificación de 12 bits es un modelo virtual opcional, NO la resolución original de ERA5.\n'+
                                   'FIR: corte 0.125 ciclos/h (propuesta fase 2, no definido en fase 1).\n'+
                                   'RMS: excluidas las primeras 20 muestras para reducir efectos de inicialización.\n',encoding='utf-8')
    if args.demo: watermark='DEMOSTRACIÓN CON DATOS SINTÉTICOS — NO RESULTADOS ERA5'
    else: watermark='DATOS HISTÓRICOS DE REFERENCIA — Open-Meteo/CSV'
    def finish(file):
        plt.figtext(.5,.01,watermark,ha='center',fontsize=8,color='dimgray')
        plt.tight_layout(rect=[0,.03,1,1]);plt.savefig(out/file,dpi=220,bbox_inches='tight');plt.close()
    hrs=np.arange(N)
    plt.figure(figsize=(11,4.5));plt.plot(hrs,s,label='Referencia histórica / simulada',lw=2);plt.plot(hrs,x,label='Entrada + ruido (σ=0.6 °C)',lw=.75,alpha=.72)
    plt.xlabel('Tiempo (h)');plt.ylabel('Temperatura (°C)');plt.title('Señal de temperatura y ruido añadido');plt.legend();plt.grid(alpha=.25);finish('fig1_entrada.png')
    plt.figure(figsize=(11,4.5));plt.plot(hrs,x,label='Entrada ruidosa',lw=.6,alpha=.45);plt.plot(hrs,s,label='Referencia',lw=1.8)
    for name,y in curves.items():plt.plot(hrs,y,label=name,lw=1.35)
    plt.xlabel('Tiempo (h)');plt.ylabel('Temperatura (°C)');plt.title('Comparación de filtros digitales');plt.legend(ncol=3,fontsize=8);plt.grid(alpha=.25);finish('fig2_filtros.png')
    plt.figure(figsize=(9,4.5));idx=np.arange(45)
    plt.step(idx,step_u,where='post',label='Escalón de referencia',lw=2,c='black')
    for name,y in step_curves.items():plt.plot(idx,y,label=name,lw=1.8)
    plt.axhline(26.5,linestyle='--',c='gray',lw=1,label='Umbral 50 %');plt.xlim(6,23)
    plt.xlabel('Tiempo (h)');plt.ylabel('Temperatura (°C)');plt.title('Retardo frente a un escalón de 25 a 28 °C');plt.legend();plt.grid(alpha=.25);finish('fig3_escalon.png')
    plt.figure(figsize=(9,4.2));plt.bar(results_df['Filtro'],results_df['Reducción RMS (%)'])
    for i,v in enumerate(results_df['Reducción RMS (%)']):plt.text(i,v+.5,f'{v:.1f} %',ha='center')
    plt.ylabel('Reducción de RMS de ruido (%)');plt.ylim(0,max(80,results_df['Reducción RMS (%)'].max()+8));plt.title('Reducción del ruido artificial');plt.grid(axis='y',alpha=.2);finish('fig4_reduccion.png')
    print('\n'+source+'\n');print('FIR h =',np.round(b_fir,8));print(results_df.to_string(index=False,float_format=lambda v:f'{v:.4f}'))
    print('\nGuardado en:',out.resolve())

if __name__=='__main__':main()
