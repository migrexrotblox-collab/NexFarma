(function(){
  const root=document.documentElement;
  function getSavedTheme(){
    try {
      const saved=localStorage.getItem('nexafarma-theme');
      if(saved==='dark'||saved==='light') return saved;
    } catch (_) {}
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches?'dark':'light';
  }
  root.dataset.theme=getSavedTheme();
  root.style.colorScheme=root.dataset.theme;
  function csrf(){return document.querySelector('meta[name="csrf-token"]')?.content||'';}
  function toast(message,kind='success'){if(!message)return;const el=document.createElement('div');el.className='flash '+kind;el.textContent=message;document.body.appendChild(el);setTimeout(()=>el.remove(),3500);}
  window.nexafarmaFormatCOP=n=>'$'+Number(n||0).toLocaleString('es-CO');
  function updateThemeIcon(){const b=document.getElementById('themeToggle');if(!b)return;const dark=root.dataset.theme==='dark';b.textContent=dark?'☀️':'🌙';b.title=dark?'Modo claro':'Modo oscuro';b.setAttribute('aria-label',b.title);}
  updateThemeIcon();
  document.getElementById('themeToggle')?.addEventListener('click',()=>{
    const next=root.dataset.theme==='dark'?'light':'dark';
    root.dataset.theme=next;
    root.style.colorScheme=next;
    try { localStorage.setItem('nexafarma-theme',next); } catch (_) {}
    updateThemeIcon();
  });
  document.getElementById('mobileMenuToggle')?.addEventListener('click',()=>{
    const nav=document.getElementById('mobileNav');
    const button=document.getElementById('mobileMenuToggle');
    const open=nav?.classList.toggle('open')||false;
    button?.setAttribute('aria-expanded',String(open));
  });
  document.querySelectorAll('[data-password-toggle]').forEach(btn=>btn.addEventListener('click',()=>{const input=document.getElementById(btn.dataset.passwordToggle);if(!input)return;input.type=input.type==='password'?'text':'password';btn.textContent=input.type==='password'?'👁':'🙈';}));
  document.querySelectorAll('.flash').forEach(el=>setTimeout(()=>el.remove(),5000));
  document.querySelectorAll('[data-favorite-form]').forEach(form=>form.addEventListener('submit',async e=>{e.preventDefault();const button=form.querySelector('button');const fd=new FormData(form);try{const r=await fetch(form.action,{method:'POST',body:fd,headers:{'X-Requested-With':'XMLHttpRequest','X-CSRFToken':csrf()}});if(r.redirected){location.href=r.url;return;}const d=await r.json();if(d.ok){button?.classList.toggle('active',d.favorite);button?.setAttribute('aria-label',d.favorite?'Quitar de favoritos':'Guardar en favoritos');toast(d.message);}}catch(_){form.submit();}}));
  document.querySelectorAll('[data-qty]').forEach(wrapper=>{const input=wrapper.querySelector('input');wrapper.querySelector('[data-qty-minus]')?.addEventListener('click',()=>{input.value=Math.max(1,Number(input.value||1)-1);input.form?.submit()});wrapper.querySelector('[data-qty-plus]')?.addEventListener('click',()=>{input.value=Math.min(20,Number(input.value||1)+1);input.form?.submit()});});
  const orderForm=document.querySelector('[data-product-base]');
  if(orderForm){
    const base=Number(orderForm.dataset.productBase||0),livePrice=document.getElementById('productLivePrice'),buttonPrice=document.getElementById('productButtonPrice'),pum=document.getElementById('productPum');
    const refreshProductPrice=()=>{
      const size=orderForm.querySelector('input[name="size"]:checked');
      const sizeExtra=Number(size?.dataset.optionPrice||0),volume=Number(size?.dataset.volumeMl||0);
      const toppings=[...orderForm.querySelectorAll('input[name="toppings"]:checked')].reduce((sum,input)=>sum+Number(input.dataset.optionPrice||0),0);
      const label=window.nexafarmaFormatCOP(base+sizeExtra+toppings);
      if(livePrice)livePrice.textContent=label;
      if(buttonPrice)buttonPrice.textContent=label;
        if(pum)pum.textContent=volume>0?'Precio por litro: '+window.nexafarmaFormatCOP(Math.round((base+sizeExtra)*1000/volume))+' / L · '+volume+' ml':'Volumen de la presentación pendiente de confirmar';
    };
    orderForm.addEventListener('change',refreshProductPrice);
    refreshProductPrice();
  }
  const back=document.getElementById('quickViewBackdrop'), content=document.getElementById('quickViewContent');
  async function openQuick(id){if(!back||!content)return;back.hidden=false;document.body.style.overflow='hidden';content.innerHTML='<div class="quick-view-media"><div class="placeholder">✚</div></div><div class="quick-view-copy"><div class="skeleton"></div><h2>Cargando…</h2></div>';try{const r=await fetch('/api/producto/'+id);const p=await r.json();if(!r.ok)throw Error();content.innerHTML='<div class="quick-view-media">'+(p.image_url?'<img src="'+esc(p.image_url)+'" alt="'+esc(p.name)+'">':'<div class="placeholder">✚</div>')+'</div><div class="quick-view-copy"><span class="eyebrow">'+esc(p.category||'Producto')+'</span><h2>'+esc(p.name)+'</h2><p class="muted">'+esc(p.description||'Consulta la descripción y las indicaciones del producto.')+'</p><div class="price-xl">'+esc(p.price_label)+'</div><p class="stock-good">'+(p.stock>0?'Disponible':'Agotado')+'</p><div class="hero-actions"><a class="btn btn-primary" href="/producto/'+p.id+'">Ver producto</a><button class="btn btn-soft" type="button" data-quick-close>Cerrar</button></div></div>';content.querySelector('[data-quick-close]')?.addEventListener('click',closeQuick);}catch(_){content.innerHTML='<div class="empty-state" style="margin:20px"><div class="emoji">✚</div><h2>No pudimos cargar este producto</h2><p>Abre la ficha completa para continuar.</p></div>';}}
  function closeQuick(){if(!back)return;back.hidden=true;document.body.style.overflow='';}
  function esc(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
  document.querySelectorAll('[data-quick-view]').forEach(b=>b.addEventListener('click',()=>openQuick(b.dataset.quickView)));document.getElementById('quickViewClose')?.addEventListener('click',closeQuick);back?.addEventListener('click',e=>{if(e.target===back)closeQuick();});document.addEventListener('keydown',e=>e.key==='Escape'&&closeQuick());
  window.addEventListener('nexafarma:toast',e=>toast(e.detail));
  const revealItems=document.querySelectorAll('[data-reveal]');
  const reduceMotion=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if(revealItems.length&&'IntersectionObserver' in window&&!reduceMotion){
    root.classList.add('has-reveal');
    const revealObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{
      if(entry.isIntersecting){entry.target.classList.add('is-visible');revealObserver.unobserve(entry.target);}
    }),{threshold:.12,rootMargin:'0px 0px -32px 0px'});
    revealItems.forEach(el=>{if(el.dataset.revealDelay)el.style.setProperty('--reveal-delay',el.dataset.revealDelay+'ms');revealObserver.observe(el);});
  }
})();
