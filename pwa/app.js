const root = document.querySelector('#app');
const state = {
  user: null,
  catalog: null,
  cart: [],
  online: navigator.onLine,
  installing: null,
};

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const currency = (value) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(value || 0));
const localISODate = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== '/api/auth/login') {
    state.user = null;
    state.catalog = null;
    state.cart = [];
    renderLogin('Sua sessão expirou. Entre novamente.');
  }
  if (!response.ok) throw new Error(data.error || 'Não foi possível concluir a solicitação.');
  return data;
}

function showBanner(message, kind = 'error', container = '#page-message') {
  const element = document.querySelector(container);
  if (!element) return;
  element.className = `banner ${kind}`;
  element.textContent = message;
  element.classList.remove('hidden');
}

function renderLogin(message = '') {
  root.innerHTML = `
    <section class="login-wrap">
      <div class="login-card">
        <div class="brand-mark" aria-hidden="true">◇</div>
        <p class="eyebrow">Sistema de Semijoias</p>
        <h1>Bem-vindo de volta</h1>
        <p class="muted">Entre para lançar uma venda e atualizar o estoque.</p>
        <div id="login-message" class="banner error ${message ? '' : 'hidden'}">${esc(message)}</div>
        <form id="login-form">
          <div class="field"><label for="email">E-mail</label><input id="email" name="email" type="email" autocomplete="username" required /></div>
          <div class="field"><label for="password">Senha</label><input id="password" name="password" type="password" autocomplete="current-password" required /></div>
          <button class="button primary" type="submit">Entrar</button>
        </form>
        <p class="install-hint">Instale este app pelo menu do navegador para abrir em tela cheia.</p>
      </div>
    </section>`;

  document.querySelector('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = 'Entrando…';
    try {
      const form = new FormData(event.currentTarget);
      const user = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ email: form.get('email'), password: form.get('password') }) });
      await openSales(user);
    } catch (error) {
      const box = document.querySelector('#login-message');
      box.textContent = error.message;
      box.className = 'banner error';
      button.disabled = false;
      button.textContent = 'Entrar';
    }
  });
}

async function loadCatalog() {
  state.catalog = await api('/api/catalog');
  return state.catalog;
}

async function openSales(user = null) {
  try {
    // Evita concorrência no refresh token rotativo do Supabase.
    state.user = user || state.user || await api('/api/me');
    state.catalog = await loadCatalog();
    renderSales();
  } catch (error) {
    if (state.user) showBanner(error.message);
    else renderLogin(error.message);
  }
}

const optionList = (rows, valueKey, labelKey, placeholder) =>
  `<option value="">${esc(placeholder)}</option>` + rows.map((row) =>
    `<option value="${esc(row[valueKey])}">${esc(row[labelKey])}</option>`
  ).join('');

function renderSales(message = '') {
  const catalog = state.catalog;
  if (!catalog) return renderLogin('Não foi possível carregar os cadastros.');
  const pendingStatus = catalog.status.find((item) => item.nome.toLowerCase() === 'pendente');
  const clients = [...catalog.clientes].sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'));
  const products = [...catalog.produtos].sort((a, b) => String(a.codigo_interno).localeCompare(String(b.codigo_interno), 'pt-BR'));
  const productOptions = '<option value="">Selecione pelo código ou descrição</option>' + products.map((product) =>
    `<option value="${esc(product.id)}">${esc(product.codigo_interno)} — ${esc(product.descricao)} · saldo ${esc(product.saldo)}</option>`
  ).join('');

  root.innerHTML = `
    <div class="app-shell">
      <header class="topbar">
        <div><p class="eyebrow">Sistema de Semijoias</p><h1>Nova venda</h1></div>
        <div class="user-actions"><span class="user-email" title="${esc(state.user?.email)}">${esc(state.user?.email)}</span><button id="logout" class="button ghost" type="button">Sair</button></div>
      </header>
      <div id="page-message" class="banner ${message ? 'success' : 'hidden'}">${esc(message)}</div>
      <div id="online-message" class="banner warning ${state.online ? 'hidden' : ''}">Você está sem conexão. As vendas só são enviadas quando a internet voltar.</div>
      <form id="sale-form">
        <section class="panel">
          <h2>Produto</h2>
          <div class="field"><label for="product">Produto com saldo</label>
            <select id="product" required>${productOptions}</select>
          </div>
          <div id="product-preview" class="empty">Escolha um produto para ver foto e saldo.</div>
          <div class="grid-two">
            <div class="field"><label for="quantity">Quantidade</label><input id="quantity" type="number" min="1" step="1" value="1" inputmode="numeric" required /></div>
            <div class="field"><label for="unit-price">Preço unitário (R$)</label><input id="unit-price" type="number" min="0" step="0.01" value="0.00" inputmode="decimal" required /></div>
          </div>
          <div class="grid-two">
            <div class="field"><label for="discount">Desconto</label><input id="discount" type="number" min="0" step="0.01" value="0" inputmode="decimal" /></div>
            <div class="field"><label for="discount-type">Desconto em</label><select id="discount-type"><option value="value">R$</option><option value="percent">%</option></select></div>
          </div>
          <button id="add-item" class="button secondary" type="button">Adicionar produto</button>
        </section>

        <section class="panel">
          <h2>Itens da venda</h2>
          <div id="cart-items" class="empty">Nenhum produto adicionado.</div>
          <div id="cart-total" class="cart-total hidden"></div>
        </section>

        <section class="panel">
          <h2>Dados da venda</h2>
          <div class="grid-two">
            <div class="field"><label for="channel">Canal</label><select id="channel" required>${optionList(catalog.canais, 'id', 'nome', 'Selecione')}</select></div>
            <div class="field"><label for="payment">Pagamento</label><select id="payment" required>${optionList(catalog.formas, 'id', 'descricao', 'Selecione')}</select></div>
          </div>
          <div id="fair-field" class="field hidden"><label for="fair">Feira</label><select id="fair">${optionList(catalog.feiras, 'id', 'nome_feira', 'Selecione a feira')}</select></div>
          <div class="grid-two">
            <div class="field"><label for="status">Status</label><select id="status" required>${optionList(catalog.status, 'id', 'nome', 'Selecione')}</select></div>
            <div class="field"><label for="sale-date">Data da venda</label><input id="sale-date" type="date" value="${localISODate()}" required /></div>
          </div>
          ${pendingStatus ? `<input id="default-status" type="hidden" value="${esc(pendingStatus.id)}" />` : ''}
          <label class="switch-row"><input id="new-client-toggle" type="checkbox" /> Cliente novo</label>
          <div id="existing-client-field" class="field"><label for="client">Cliente</label>
            <select id="client" required>${optionList(clients, 'id', 'nome', 'Selecione um cliente')}</select>
          </div>
          <div id="new-client-fields" class="hidden">
            <div class="field"><label for="new-client-name">Nome do cliente</label><input id="new-client-name" maxlength="150" /></div>
            <div class="field"><label for="new-client-phone">Telefone (opcional)</label><input id="new-client-phone" type="tel" inputmode="tel" placeholder="(11) 91234-5678" /></div>
          </div>
          <button id="save-sale" class="button primary" type="submit" ${state.online ? '' : 'disabled'}>Salvar venda</button>
        </section>
      </form>
    </div>`;

  if (pendingStatus) document.querySelector('#status').value = String(pendingStatus.id);
  document.querySelector('#logout').addEventListener('click', async () => {
    try { await api('/api/auth/logout', { method: 'POST', body: '{}' }); } catch (_) {}
    state.user = null; state.catalog = null; state.cart = [];
    renderLogin();
  });

  const productSelect = document.querySelector('#product');
  productSelect.addEventListener('change', () => updateProductPreview(products));
  document.querySelector('#add-item').addEventListener('click', () => addCartItem(products));
  document.querySelector('#channel').addEventListener('change', (event) => {
    const selected = catalog.canais.find((item) => String(item.id) === event.target.value);
    const field = document.querySelector('#fair-field');
    const isFair = selected?.nome.trim().toLowerCase() === 'feira';
    field.classList.toggle('hidden', !isFair);
    document.querySelector('#fair').required = isFair;
  });
  document.querySelector('#new-client-toggle').addEventListener('change', (event) => {
    const isNew = event.target.checked;
    document.querySelector('#existing-client-field').classList.toggle('hidden', isNew);
    document.querySelector('#client').required = !isNew;
    document.querySelector('#new-client-fields').classList.toggle('hidden', !isNew);
    document.querySelector('#new-client-name').required = isNew;
  });
  document.querySelector('#sale-form').addEventListener('submit', submitSale);
  renderCart();
  updateOnlineUI();
}

function updateProductPreview(products) {
  const selected = products.find((product) => String(product.id) === document.querySelector('#product').value);
  const preview = document.querySelector('#product-preview');
  const price = document.querySelector('#unit-price');
  if (!selected) {
    preview.className = 'empty';
    preview.textContent = 'Escolha um produto para ver foto e saldo.';
    price.value = '0.00';
    return;
  }
  price.value = Number(selected.preco || 0).toFixed(2);
  const photo = String(selected.foto_url || '').trim();
  preview.className = 'product-preview';
  preview.innerHTML = `${photo.startsWith('https://') ? `<img class="product-photo" src="${esc(photo)}" alt="Foto de ${esc(selected.descricao)}" />` : '<div class="product-photo product-placeholder" aria-hidden="true">◇</div>'}
    <div><strong>${esc(selected.codigo_interno)} — ${esc(selected.descricao)}</strong>
    <div class="product-meta">Saldo disponível: ${esc(selected.saldo)}<br>Preço sugerido: ${currency(selected.preco)}</div></div>`;
}

function addCartItem(products) {
  const product = products.find((item) => String(item.id) === document.querySelector('#product').value);
  const message = document.querySelector('#page-message');
  if (!product) return showBanner('Selecione um produto.', 'error');
  const quantity = Number(document.querySelector('#quantity').value);
  const price = Number(document.querySelector('#unit-price').value);
  const discount = Number(document.querySelector('#discount').value || 0);
  const discountType = document.querySelector('#discount-type').value;
  const alreadyAdded = state.cart.filter((item) => item.product_id === product.id).reduce((sum, item) => sum + item.quantity, 0);
  if (!Number.isInteger(quantity) || quantity < 1) return showBanner('Informe uma quantidade inteira maior que zero.');
  if (quantity + alreadyAdded > Number(product.saldo)) return showBanner(`Saldo insuficiente. Disponível: ${Number(product.saldo) - alreadyAdded}.`);
  if (!Number.isFinite(price) || price < 0 || !Number.isFinite(discount) || discount < 0) return showBanner('Confira preço e desconto.');
  const subtotal = quantity * price;
  const discountValue = discountType === 'percent' ? subtotal * discount / 100 : discount;
  if (discountValue > subtotal) return showBanner('O desconto não pode superar o total do item.');
  state.cart.push({ product_id: product.id, product, quantity, unit_price: price, discount, discount_type: discountType });
  message.classList.add('hidden');
  document.querySelector('#quantity').value = '1';
  document.querySelector('#discount').value = '0';
  renderCart();
}

function itemTotal(item) {
  const subtotal = item.quantity * item.unit_price;
  const discount = item.discount_type === 'percent' ? subtotal * item.discount / 100 : item.discount;
  return Math.round((subtotal - discount + Number.EPSILON) * 100) / 100;
}

function renderCart() {
  const target = document.querySelector('#cart-items');
  const total = document.querySelector('#cart-total');
  if (!target || !total) return;
  if (!state.cart.length) {
    target.className = 'empty';
    target.textContent = 'Nenhum produto adicionado.';
    total.classList.add('hidden');
    return;
  }
  target.className = '';
  target.innerHTML = state.cart.map((item, index) => `
    <div class="cart-item"><div><strong>${esc(item.product.codigo_interno)} — ${esc(item.product.descricao)}</strong>
      <small>${item.quantity} × ${currency(item.unit_price)}${item.discount ? ` · desconto ${item.discount_type === 'percent' ? `${item.discount}%` : currency(item.discount)}` : ''}<br>Total ${currency(itemTotal(item))}</small></div>
      <button class="button remove" type="button" data-remove="${index}" aria-label="Remover ${esc(item.product.descricao)}">Remover</button></div>`).join('');
  total.innerHTML = `<span>Total da venda</span><span>${currency(state.cart.reduce((sum, item) => sum + itemTotal(item), 0))}</span>`;
  total.classList.remove('hidden');
  target.querySelectorAll('[data-remove]').forEach((button) => button.addEventListener('click', () => {
    state.cart.splice(Number(button.dataset.remove), 1);
    renderCart();
  }));
}

async function submitSale(event) {
  event.preventDefault();
  if (!state.online) return showBanner('Conecte-se à internet para salvar a venda.', 'warning');
  if (!state.cart.length) return showBanner('Adicione pelo menos um produto.');
  const newClient = document.querySelector('#new-client-toggle').checked;
  const channel = document.querySelector('#channel');
  const payload = {
    canal_id: Number(channel.value),
    feira_id: document.querySelector('#fair').value ? Number(document.querySelector('#fair').value) : null,
    forma_id: Number(document.querySelector('#payment').value),
    status_id: Number(document.querySelector('#status').value),
    data_venda: document.querySelector('#sale-date').value,
    cliente_id: newClient ? null : Number(document.querySelector('#client').value),
    cliente_novo: newClient ? {
      nome: document.querySelector('#new-client-name').value.trim(),
      telefone: document.querySelector('#new-client-phone').value.trim(),
    } : null,
    items: state.cart.map(({ product_id, quantity, unit_price, discount, discount_type }) => ({ product_id, quantity, unit_price, discount, discount_type })),
  };
  const button = document.querySelector('#save-sale');
  button.disabled = true;
  button.textContent = 'Salvando…';
  try {
    const result = await api('/api/sales', { method: 'POST', body: JSON.stringify(payload) });
    const done = new Set((result.created || []).map((item) => item.index));
    const failed = result.failures || [];
    state.cart = state.cart.filter((_, index) => !done.has(index));
    if (failed.length) {
      renderCart();
      showBanner(`${done.size} item(ns) registrado(s). ${failed.length} permaneceram no carrinho: ${failed.map((item) => item.message).join(' · ')}`, 'warning');
      button.disabled = !state.online;
      button.textContent = 'Salvar venda';
      return;
    }
    state.cart = [];
    await loadCatalog();
    renderSales(`${done.size} item(ns) registrados e estoque atualizado.`);
  } catch (error) {
    showBanner(error.message);
    button.disabled = !state.online;
    button.textContent = 'Salvar venda';
  }
}

function updateOnlineUI() {
  state.online = navigator.onLine;
  const banner = document.querySelector('#online-message');
  const save = document.querySelector('#save-sale');
  if (banner) banner.classList.toggle('hidden', state.online);
  if (save) save.disabled = !state.online;
}

window.addEventListener('online', updateOnlineUI);
window.addEventListener('offline', updateOnlineUI);
window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  state.installing = event;
});

api('/api/me').then(async (user) => {
  await openSales(user);
}).catch(() => renderLogin(navigator.onLine ? '' : 'Conecte-se à internet para entrar e registrar vendas.'));
