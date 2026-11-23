// Sons (fase 4 de UX, porte do Office): [t0,dur,f0,f1,onda,pico]
const E="exponentialRampToValueAtTime",n=(a,b,d=.09,p=.08,t=0,o="sine")=>[t,d,a,b||a,o,p];
export const SONS={gravar:[n(660,880)],parar:[n(880,660)],concluido:[n(523,0,.11,.07),n(784,0,.11,.07,.11)],
aprovacao:[0,.13].map(t=>n(740,0,.07,.08,t,"triangle")),erro:[n(220,180,.2,.07)],respondeu:[n(988,0,.07,.05)]};
export const VIB={gravar:[10],parar:[10],concluido:[20,40,20],aprovacao:[30,60,30],erro:[60]};
export const VOL={baixo:.5,medio:.75,alto:1};
export const lerPrefs=(st,g=k=>{try{return st.getItem(k)}catch{}},v=g("somVol"))=>
({on:g("som")!="0",vol:VOL[v]>0?v:"medio",vib:g("vibrar")!="0"});
export const criarSom=(o,u=-1e9,m=new Map)=>(ev,k)=>{
const N=SONS[ev],p=N&&o.prefs(),c=p?.on&&o.visivel()&&o.ctx(),t=o.agora();
if(!c||t-u<2e3&&!/^[gp][ra]/.test(ev)||t-m.get(k)<6e4)return!1;
u=t;k&&m.set(k,t);p.vib&&VIB[ev]&&o.vibrar(VIB[ev]);
const P=()=>N.map(([s,d,a,b,w,q])=>{
const t=c.currentTime+s,x=c.createOscillator(),g=c.createGain(),f=x.frequency,v=g.gain;
x.type=w;f.setValueAtTime(a,t);b!=a&&f[E](b,t+d);
v.setValueAtTime(0,t);v.linearRampToValueAtTime(q*VOL[p.vol],t+.005);v[E](1e-4,t+d);
x.connect(g).connect(c.destination);x.start(t);x.stop(t+d)});
c.state!="running"?c.resume().then(()=>o.agora()-t<500&&P(),()=>{}):P();
return!0};
const ak=a=>"apr:"+a.fonte+"/"+a.ficheiro,fim=(t,j,s=/^(conclu(ída|ido)$|parcial)/.test(t.status)?!(j&&t.status=="concluída")&&"concluido":"erro")=>
s&&!t.running&&!/^(a correr|parada\?|morreu|\?)$/.test(t.status)&&[s,`fim:${t.squad}:${t.id}:${t.finished||""}`];
export const sonsDoPoll=(p,s,j,a=p.aprovacoes.map(ak))=>[
...s.alerts.items.filter(k=>/^tarefa:/.test(k)&&!p.alerts.items.includes(k)).map(k=>["erro",k]),
...s.tasks.map(t=>p.tasks.some(o=>o.running&&o.id==t.id)&&fim(t,j)).filter(f=>f),
...s.aprovacoes.map(ak).filter(k=>!a.includes(k)).map(k=>["aprovacao",k])];
