const root = document.querySelector('#app');
const state = {
  user: null,
  catalog: null,
  screen: 'sales',
  cart: [],
  online: navigator.onLine,
  installing: null,
  productMode: 'list',
  clientsOffset: 0,
  clientsSearch: '',
  clientsChannel: '',
  clientsTotal: 0,
  clients: [],
  clientEdit: null,
  clientDetail: null,
  clientReturnDetail: false,
  historyParams: { period: 'month', channel_id: '', client: '', start: '', end: '', offset: 0 },
  historyItems: [],
  historyCount: 0,
  historySummary: null,
};

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const currency = (value) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(value || 0));
const localISODate = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};
const formatDate = (value, compact = false) => {
  if (!value) return '—';
  const date = new Date(`${String(value).slice(0, 10)}T12:00:00`);
  return new Intl.DateTimeFormat('pt-BR', compact ? { day: '2-digit', month: '2-digit', year: '2-digit' } : { day: '2-digit', month: '2-digit', year: 'numeric' }).format(date);
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
          <div class="segmented-control" role="group" aria-label="Forma de localizar produto">
            <button id="product-mode-list" type="button" class="${state.productMode === 'list' ? 'selected' : ''}">Escolher na lista</button>
            <button id="product-mode-code" type="button" class="${state.productMode === 'code' ? 'selected' : ''}">Digitar código</button>
          </div>
          <div id="product-list-field" class="field ${state.productMode === 'list' ? '' : 'hidden'}"><label for="product-search">Produto com saldo</label>
            <div class="product-picker" id="product-picker">
              <div class="product-picker-control"><input id="product-search" type="search" role="combobox" aria-autocomplete="list" aria-controls="product-options" aria-expanded="false" autocomplete="off" placeholder="Buscar código ou descrição" /><button id="product-picker-toggle" type="button" aria-label="Mostrar produtos" aria-expanded="false">⌄</button></div>
              <div id="product-options" class="product-options hidden" role="listbox" aria-label="Produtos disponíveis"></div>
            </div>
            <select id="product" class="internal-select" tabindex="-1" aria-hidden="true">${productOptions}</select>
          </div>
          <div id="product-code-field" class="field ${state.productMode === 'code' ? '' : 'hidden'}">
            <label for="product-code">Código interno</label>
            <div class="inline-search"><input id="product-code" type="search" inputmode="text" autocomplete="off" placeholder="Digite o código do produto" /><button id="find-product-code" class="button secondary" type="button">Buscar</button></div>
            <small id="product-code-message" class="field-hint">Digite o código completo, como aparece na etiqueta.</small>
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
          <div id="sale-progress" class="sale-progress hidden" role="status" aria-live="polite"></div>
        </section>
      </form>
      ${renderNav('sales')}
    </div>`;

  if (pendingStatus) document.querySelector('#status').value = String(pendingStatus.id);
  enhanceDropdowns();
  document.querySelector('#logout').addEventListener('click', async () => {
    try { await api('/api/auth/logout', { method: 'POST', body: '{}' }); } catch (_) {}
    state.user = null; state.catalog = null; state.cart = [];
    renderLogin();
  });

  const productSelect = document.querySelector('#product');
  document.querySelector('#product-search').addEventListener('input', () => filterProductOptions(products));
  document.querySelector('#product-search').addEventListener('focus', () => filterProductOptions(products, true));
  document.querySelector('#product-picker-toggle').addEventListener('click', () => {
    const options = document.querySelector('#product-options');
    if (options.classList.contains('hidden')) {
      document.querySelector('#product-search').focus();
      filterProductOptions(products, true);
    } else closeProductOptions();
  });
  document.querySelector('#product-search').addEventListener('keydown', (event) => {
    const options = document.querySelector('#product-options');
    const choices = [...options.querySelectorAll('[role="option"]')];
    if (event.key === 'Escape') closeProductOptions();
    if (event.key === 'ArrowDown' && choices.length) { event.preventDefault(); choices[0].focus(); }
    if (event.key === 'Enter' && !options.classList.contains('hidden') && choices.length) { event.preventDefault(); choices[0].click(); }
  });
  document.addEventListener('click', (event) => {
    if (!document.querySelector('#product-picker')?.contains(event.target)) closeProductOptions();
  });
  productSelect.addEventListener('change', () => {
    const selected = products.find((product) => String(product.id) === productSelect.value);
    if (selected) {
      document.querySelector('#product-code').value = String(selected.codigo_interno || '');
      document.querySelector('#product-search').value = String(selected.codigo_interno || '');
    }
    filterProductOptions(products);
  });
  document.querySelector('#product-mode-list').addEventListener('click', () => setProductMode('list'));
  document.querySelector('#product-mode-code').addEventListener('click', () => setProductMode('code'));
  document.querySelector('#find-product-code').addEventListener('click', () => selectProductByCode(products));
  document.querySelector('#product-code').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); selectProductByCode(products); }
  });
  document.querySelector('#product-code').addEventListener('input', () => {
    const selected = products.find((product) => String(product.id) === productSelect.value);
    if (selected && String(selected.codigo_interno || '').toLowerCase() !== document.querySelector('#product-code').value.trim().toLowerCase()) {
      productSelect.value = '';
      updateProductPreview(products);
    }
  });
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
  attachNav();
  renderCart();
  updateOnlineUI();
}

function setProductMode(mode) {
  state.productMode = mode;
  const isList = mode === 'list';
  document.querySelector('#product-list-field').classList.toggle('hidden', !isList);
  document.querySelector('#product-code-field').classList.toggle('hidden', isList);
  if (!isList) closeProductOptions();
  document.querySelector('#product-mode-list').classList.toggle('selected', isList);
  document.querySelector('#product-mode-code').classList.toggle('selected', !isList);
  document.querySelector('#product-code-message').textContent = 'Digite o código completo, como aparece na etiqueta.';
}

function closeProductOptions() {
  const options = document.querySelector('#product-options');
  const search = document.querySelector('#product-search');
  const toggle = document.querySelector('#product-picker-toggle');
  if (!options || !search || !toggle) return;
  options.classList.add('hidden');
  search.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-expanded', 'false');
}

function filterProductOptions(products, open = true) {
  const select = document.querySelector('#product');
  const current = select.value;
  const query = document.querySelector('#product-search').value.trim().toLocaleLowerCase('pt-BR');
  const matches = products.filter((product) =>
    `${product.codigo_interno || ''} ${product.descricao || ''}`.toLocaleLowerCase('pt-BR').includes(query)
  );
  select.innerHTML = '<option value="">Selecione um produto</option>' + products.map((product) =>
    `<option value="${esc(product.id)}">${esc(product.codigo_interno)} — ${esc(product.descricao)} · saldo ${esc(product.saldo)}</option>`
  ).join('');
  if (matches.some((product) => String(product.id) === current)) select.value = current;
  const list = document.querySelector('#product-options');
  list.innerHTML = matches.length ? matches.slice(0, 80).map((product) => `
    <button type="button" class="product-option" role="option" aria-selected="${String(product.id) === current}" data-product-id="${esc(product.id)}">
      <span class="product-option-main"><strong>${esc(product.codigo_interno)} — ${esc(product.descricao)}</strong><small>Saldo disponível: ${esc(product.saldo)}</small></span>
      <span class="product-option-check" aria-hidden="true">${String(product.id) === current ? '✓' : ''}</span>
    </button>`).join('') : '<div class="product-options-empty">Nenhum produto encontrado.</div>';
  list.querySelectorAll('[data-product-id]').forEach((button) => button.addEventListener('click', () => {
    select.value = button.dataset.productId;
    const selected = products.find((product) => String(product.id) === select.value);
    document.querySelector('#product-search').value = selected ? `${selected.codigo_interno} — ${selected.descricao}` : '';
    document.querySelector('#product-code').value = selected?.codigo_interno || '';
    closeProductOptions();
    updateProductPreview(products);
  }));
  list.querySelectorAll('[role="option"]').forEach((button) => button.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const all = [...list.querySelectorAll('[role="option"]')];
    const next = all.indexOf(button) + (event.key === 'ArrowDown' ? 1 : -1);
    (all[next] || document.querySelector('#product-search')).focus();
  }));
  if (open) {
    list.classList.remove('hidden');
    document.querySelector('#product-search').setAttribute('aria-expanded', 'true');
    document.querySelector('#product-picker-toggle').setAttribute('aria-expanded', 'true');
  }
  updateProductPreview(products);
}

function selectProductByCode(products) {
  const code = document.querySelector('#product-code').value.trim().toLocaleLowerCase('pt-BR');
  const match = products.find((product) => String(product.codigo_interno || '').trim().toLocaleLowerCase('pt-BR') === code);
  const message = document.querySelector('#product-code-message');
  if (!match) {
    message.textContent = 'Não encontrei esse código entre os produtos com saldo.';
    message.classList.add('field-hint-error');
    return;
  }
  document.querySelector('#product').value = String(match.id);
  document.querySelector('#product-search').value = `${match.codigo_interno} — ${match.descricao}`;
  closeProductOptions();
  message.textContent = `${match.codigo_interno} selecionado.`;
  message.classList.remove('field-hint-error');
  updateProductPreview(products);
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
  const progress = document.querySelector('#sale-progress');
  button.disabled = true;
  button.innerHTML = '<span class="button-spinner" aria-hidden="true"></span> Salvando venda…';
  progress.className = 'sale-progress pending';
  progress.innerHTML = '<span class="hourglass" aria-hidden="true">⏳</span><span>Registrando produtos e atualizando o estoque…</span>';
  try {
    const result = await api('/api/sales', { method: 'POST', body: JSON.stringify(payload) });
    const done = new Set((result.created || []).map((item) => item.index));
    const failed = result.failures || [];
    state.cart = state.cart.filter((_, index) => !done.has(index));
    if (failed.length) {
      renderCart();
      showBanner(`${done.size} item(ns) registrado(s). ${failed.length} permaneceram no carrinho: ${failed.map((item) => item.message).join(' · ')}`, 'warning');
      progress.className = 'sale-progress error';
      progress.textContent = `⚠️ ${failed.length} item(ns) não foram registrados. Confira o aviso e tente novamente.`;
      button.disabled = !state.online;
      button.textContent = 'Tentar novamente';
      return;
    }
    state.cart = [];
    progress.className = 'sale-progress success';
    progress.textContent = `✅ ${done.size} item(ns) registrado(s) e estoque atualizado com sucesso.`;
    let refreshMessage = `${done.size} item(ns) registrados e estoque atualizado com sucesso.`;
    try {
      await loadCatalog();
    } catch (_) {
      refreshMessage += ' Atualize a tela para carregar os saldos mais recentes.';
    }
    renderSales(refreshMessage);
  } catch (error) {
    showBanner(error.message);
    progress.className = 'sale-progress error';
    progress.textContent = `❌ Não foi possível salvar a venda: ${error.message}`;
    button.disabled = !state.online;
    button.textContent = 'Tentar novamente';
  }
}

function updateOnlineUI() {
  state.online = navigator.onLine;
  const banner = document.querySelector('#online-message');
  const save = document.querySelector('#save-sale');
  if (banner) banner.classList.toggle('hidden', state.online);
  if (save) save.disabled = !state.online;
  document.querySelectorAll('[data-online-only]').forEach((button) => { button.disabled = !state.online; });
}

const NAV_ITEMS = [
  ['sales', '💰', 'Venda'], ['clients', '👤', 'Clientes'], ['history', '🧾', 'Histórico'], ['settings', '⚙️', 'Config'],
];

function renderNav(active) {
  return `<nav class="bottom-nav" aria-label="Navegação principal">${NAV_ITEMS.map(([id, icon, label]) =>
    `<button type="button" data-route="${id}" class="nav-button ${active === id ? 'active' : ''}" aria-current="${active === id ? 'page' : 'false'}"><span aria-hidden="true">${icon}</span><small>${label}</small></button>`
  ).join('')}</nav>`;
}

function attachNav() {
  document.querySelectorAll('[data-route]').forEach((button) => button.addEventListener('click', () => {
    const route = button.dataset.route;
    state.screen = route;
    if (route === 'sales') renderSales();
    else if (route === 'clients') { state.clientDetail = null; openClients(true); }
    else if (route === 'history') openHistory(true);
    else openSettings();
  }));
}

function shellMarkup(title, active, body) {
  return `<div class="app-shell">
    <header class="topbar"><div><p class="eyebrow">Sistema de Semijoias</p><h1>${title}</h1></div>
      <div class="user-actions"><span class="user-email" title="${esc(state.user?.email)}">${esc(state.user?.email)}</span><button id="logout" class="button ghost" type="button">Sair</button></div>
    </header>
    <div id="page-message" class="banner hidden"></div>
    <div id="online-message" class="banner warning ${state.online ? 'hidden' : ''}">Você está sem conexão. As consultas podem estar desatualizadas; vendas e cadastros exigem conexão.</div>
    ${body}
    ${renderNav(active)}
  </div>`;
}

function bindShell() {
  document.querySelector('#logout')?.addEventListener('click', logoutFromPwa);
  enhanceDropdowns();
  attachNav();
  updateOnlineUI();
}

function enhanceDropdowns() {
  if (!document.documentElement.dataset.dropdownOutsideHandler) {
    document.documentElement.dataset.dropdownOutsideHandler = 'true';
    document.addEventListener('click', (event) => {
      if (!event.target.closest('.select-picker')) {
        document.querySelectorAll('.select-picker-menu').forEach((menu) => menu.classList.add('hidden'));
        document.querySelectorAll('.select-picker-trigger').forEach((button) => button.setAttribute('aria-expanded', 'false'));
      }
    });
  }
  document.querySelectorAll('select:not(.internal-select):not([data-custom-dropdown])').forEach((select) => {
    select.dataset.customDropdown = 'true';
    const picker = document.createElement('div');
    picker.className = 'select-picker';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'select-picker-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    const label = document.createElement('span');
    const arrow = document.createElement('span');
    arrow.className = 'select-picker-arrow';
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '⌄';
    trigger.append(label, arrow);
    const menu = document.createElement('div');
    menu.className = 'select-picker-menu hidden';
    menu.setAttribute('role', 'listbox');
    picker.append(trigger, menu);
    select.classList.add('select-picker-source');
    select.insertAdjacentElement('afterend', picker);

    const sync = () => {
      const option = select.selectedOptions[0];
      label.textContent = option?.textContent || 'Selecione';
      trigger.disabled = select.disabled;
      menu.innerHTML = [...select.options].map((item, index) => `
        <button type="button" role="option" class="select-picker-option ${item.disabled ? 'disabled' : ''} ${item.selected ? 'selected' : ''}" aria-selected="${item.selected}" data-index="${index}" ${item.disabled ? 'disabled' : ''}>${esc(item.textContent)}</button>`).join('');
      menu.querySelectorAll('[data-index]').forEach((button) => button.addEventListener('click', () => {
        select.selectedIndex = Number(button.dataset.index);
        select.dispatchEvent(new Event('change', { bubbles: true }));
        menu.classList.add('hidden');
        trigger.setAttribute('aria-expanded', 'false');
      }));
    };
    select.addEventListener('change', sync);
    trigger.addEventListener('click', () => {
      const opening = menu.classList.contains('hidden');
      document.querySelectorAll('.select-picker-menu').forEach((other) => other.classList.add('hidden'));
      menu.classList.toggle('hidden', !opening);
      trigger.setAttribute('aria-expanded', String(opening));
    });
    sync();
  });
}

async function logoutFromPwa() {
  try { await api('/api/auth/logout', { method: 'POST', body: '{}' }); } catch (_) {}
  state.user = null;
  state.catalog = null;
  state.cart = [];
  state.clients = [];
  state.historyItems = [];
  renderLogin();
}

function clientQueryUrl(offset = 0) {
  const params = new URLSearchParams({ offset: String(offset), limit: '20' });
  if (state.clientsSearch) params.set('search', state.clientsSearch);
  if (state.clientsChannel) params.set('channel_id', state.clientsChannel);
  return `/api/clients?${params}`;
}

async function openClients(reset = false, message = '') {
  if (reset) state.clientsOffset = 0;
  if (state.clientEdit) return renderClientForm();
  if (state.clientDetail) return renderClientDetail(message);
  try {
    const requestedOffset = state.clientsOffset;
    const result = await api(clientQueryUrl(requestedOffset));
    state.clients = requestedOffset ? [...state.clients, ...(result.items || [])] : result.items || [];
    state.clientsTotal = result.total || 0;
    renderClients(message);
  } catch (error) {
    if (!state.user) return;
    renderClients();
    showBanner(error.message);
  }
}

function renderClients(message = '') {
  if (state.clientEdit) return renderClientForm();
  const channels = state.catalog?.canais || [];
  const clientsMarkup = state.clients.length ? state.clients.map((client) => {
    const channel = channels.find((item) => String(item.id) === String(client.canal_id))?.nome || 'Canal não informado';
    const phoneDigits = String(client.telefone || '').replace(/\D/g, '');
    const whatsapp = phoneDigits.length >= 10 ? `<a class="button ghost" href="https://wa.me/55${esc(phoneDigits)}" target="_blank" rel="noopener noreferrer">WhatsApp</a>` : '';
    return `<article class="client-card">
      <div class="client-heading"><div><strong>${esc(client.nome)}</strong><small>${esc(channel)}${client.telefone ? ` · ${esc(client.telefone)}` : ''}</small></div></div>
      <div class="card-actions">${whatsapp}<button class="button secondary" type="button" data-consult-client="${esc(client.id)}">Consultar dados</button></div>
    </article>`;
  }).join('') : '<p class="empty">Nenhum cliente encontrado.</p>';
  const channelOptions = '<option value="">Todos os canais</option>' + channels.map((item) => `<option value="${esc(item.id)}" ${String(item.id) === state.clientsChannel ? 'selected' : ''}>${esc(item.nome)}</option>`).join('');
  root.innerHTML = shellMarkup('Clientes', 'clients', `
    <section class="panel">
      <form id="clients-filter" class="filter-grid">
        <div class="field"><label for="clients-search">Buscar cliente</label><input id="clients-search" value="${esc(state.clientsSearch)}" placeholder="Nome do cliente" /></div>
        <div class="field"><label for="clients-channel">Canal</label><select id="clients-channel">${channelOptions}</select></div>
        <button class="button secondary" type="submit">Buscar</button>
        <button id="new-client" class="button primary" type="button">＋ Novo cliente</button>
      </form>
      <p class="muted result-count">${state.clientsTotal} cliente(s)</p>
      <div class="client-list">${clientsMarkup}</div>
      <div id="client-pagination" class="pagination ${state.clients.length < state.clientsTotal ? '' : 'hidden'}">
        <button id="more-clients" class="button secondary" type="button">Mostrar mais</button>
      </div>
    </section>`);
  bindShell();
  if (message) showBanner(message, 'success');
  document.querySelector('#clients-filter').addEventListener('submit', (event) => {
    event.preventDefault();
    state.clientsSearch = document.querySelector('#clients-search').value.trim();
    state.clientsChannel = document.querySelector('#clients-channel').value;
    state.clientDetail = null;
    openClients(true);
  });
  document.querySelector('#new-client').addEventListener('click', () => {
    state.clientReturnDetail = false;
    state.clientEdit = { isNew: true };
    renderClientForm();
  });
  document.querySelector('#more-clients').addEventListener('click', () => {
    state.clientsOffset = state.clients.length;
    openClients();
  });
  document.querySelectorAll('[data-consult-client]').forEach((button) => button.addEventListener('click', () => {
    const item = state.clients.find((client) => String(client.id) === button.dataset.consultClient);
    if (item) openClientDetail(item);
  }));
}

async function openClientDetail(client, message = '') {
  state.clientDetail = client;
  state.purchaseDetails = null;
  renderClientDetail(message);
  try {
    const details = await api(`/api/clients/${client.id}/purchases`);
    if (String(state.clientDetail?.id) !== String(client.id)) return;
    state.purchaseDetails = details;
    renderClientDetail(message);
  } catch (error) {
    if (!state.user) return;
    renderClientDetail(message);
    showBanner(error.message);
  }
}

function renderClientDetail(message = '') {
  const client = state.clientDetail;
  if (!client) return openClients(true);
  const channels = state.catalog?.canais || [];
  const fairs = state.catalog?.feiras || [];
  const channel = channels.find((item) => String(item.id) === String(client.canal_id))?.nome || '—';
  const fair = fairs.find((item) => String(item.id) === String(client.detalhe_feira_id))?.nome_feira || '—';
  const isFair = channel.trim().toLowerCase() === 'feira';
  root.innerHTML = shellMarkup('Consultar cliente', 'clients', `
    <section class="panel">
      <div class="detail-title"><div><p class="eyebrow">Cadastro do cliente</p><h2>${esc(client.nome)}</h2></div></div>
      <div class="detail-grid">
        <div class="detail-cell"><small>Telefone</small><strong>${esc(client.telefone || '—')}</strong></div>
        <div class="detail-cell"><small>Canal</small><strong>${esc(channel)}</strong></div>
        ${isFair ? `<div class="detail-cell"><small>Feira</small><strong>${esc(fair)}</strong></div>` : ''}
        <div class="detail-cell"><small>Nascimento</small><strong>${formatDate(client.data_nascimento)}</strong></div>
        <div class="detail-cell"><small>Casamento</small><strong>${formatDate(client.data_casamento)}</strong></div>
      </div>
      <div class="form-actions detail-actions"><button id="back-to-clients" class="button secondary" type="button">Voltar</button><button id="edit-client-detail" class="button primary" type="button">Editar cadastro</button></div>
    </section>
    <section class="panel"><h2>Compras</h2>${renderClientPurchases()}</section>`);
  bindShell();
  if (message) showBanner(message, 'success');
  document.querySelector('#back-to-clients').addEventListener('click', () => {
    state.clientDetail = null;
    state.purchaseDetails = null;
    openClients(true);
  });
  document.querySelector('#edit-client-detail').addEventListener('click', () => {
    state.clientReturnDetail = true;
    state.clientEdit = client;
    renderClientForm();
  });
}

function renderClientPurchases() {
  const data = state.purchaseDetails;
  if (!data) return '<div class="purchase-detail muted">Carregando compras…</div>';
  const items = data.items?.length ? data.items.map((sale) => `
    <div class="purchase-row"><span>${formatDate(sale.data_venda)} · ${esc(sale.produto?.descricao || 'Produto')}</span><strong>${currency(sale.valor_final)}</strong>
      <small>${esc(sale.quantidade)} un. · ${esc(sale.produto?.codigo_interno || '')}${sale.pagamento?.descricao ? ` · ${esc(sale.pagamento.descricao)}` : ''}</small></div>`).join('') : '<p class="empty">Este cliente ainda não possui compras.</p>';
  return `<div class="purchase-detail"><div class="purchase-summary"><span>${data.count} compra(s)</span><strong>${currency(data.total)}</strong><small>Última: ${formatDate(data.latest)}</small></div>${items}</div>`;
}

function renderClientForm() {
  const client = state.clientEdit || { isNew: true };
  const isNew = Boolean(client.isNew);
  const channels = state.catalog?.canais || [];
  const fairs = state.catalog?.feiras || [];
  const channelOptions = optionList(channels, 'id', 'nome', 'Escolha um canal');
  const fairOptions = optionList(fairs, 'id', 'nome_feira', 'Escolha uma feira');
  const channelName = channels.find((item) => String(item.id) === String(client.canal_id))?.nome || '';
  const isFair = channelName.trim().toLowerCase() === 'feira';
  root.innerHTML = shellMarkup(isNew ? 'Novo cliente' : 'Editar cliente', 'clients', `
    <form id="client-form" class="panel">
      <div class="field"><label for="client-name">Nome *</label><input id="client-name" maxlength="150" value="${esc(client.nome || '')}" required /></div>
      <div class="field"><label for="client-phone">Telefone</label><input id="client-phone" type="tel" inputmode="tel" placeholder="(11) 91234-5678" value="${esc(client.telefone || '')}" /></div>
      <div class="field"><label for="client-channel">Canal *</label><select id="client-channel" required>${channelOptions}</select></div>
      <div id="client-fair-field" class="field ${isFair ? '' : 'hidden'}"><label for="client-fair">Feira *</label><select id="client-fair">${fairOptions}</select></div>
      <div class="grid-two">
        <div class="field"><label for="client-birth">Nascimento</label><input id="client-birth" type="date" max="${localISODate()}" value="${esc(client.data_nascimento || '')}" /></div>
        <div class="field"><label for="client-wedding">Casamento</label><input id="client-wedding" type="date" max="${localISODate()}" value="${esc(client.data_casamento || '')}" /></div>
      </div>
      <div class="form-actions"><button id="cancel-client" class="button secondary" type="button">Cancelar</button><button class="button primary" data-online-only type="submit">Salvar cliente</button></div>
    </form>`);
  bindShell();
  if (client.canal_id) document.querySelector('#client-channel').value = String(client.canal_id);
  document.querySelector('#client-fair').required = isFair;
  if (client.detalhe_feira_id) document.querySelector('#client-fair').value = String(client.detalhe_feira_id);
  document.querySelector('#client-channel').addEventListener('change', (event) => {
    const selected = channels.find((item) => String(item.id) === event.target.value);
    const required = selected?.nome.trim().toLowerCase() === 'feira';
    document.querySelector('#client-fair-field').classList.toggle('hidden', !required);
    document.querySelector('#client-fair').required = required;
  });
  document.querySelector('#cancel-client').addEventListener('click', () => {
    state.clientEdit = null;
    if (state.clientReturnDetail && state.clientDetail) {
      state.clientReturnDetail = false;
      renderClientDetail();
    } else {
      state.clientReturnDetail = false;
      openClients(true);
    }
  });
  document.querySelector('#client-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const channelId = Number(document.querySelector('#client-channel').value);
    const selected = channels.find((item) => Number(item.id) === channelId);
    const fairId = selected?.nome.trim().toLowerCase() === 'feira' ? Number(document.querySelector('#client-fair').value) : null;
    const payload = {
      nome: document.querySelector('#client-name').value.trim(),
      telefone: document.querySelector('#client-phone').value.trim(),
      canal_id: channelId,
      detalhe_feira_id: fairId,
      data_nascimento: document.querySelector('#client-birth').value || null,
      data_casamento: document.querySelector('#client-wedding').value || null,
    };
    const submit = event.currentTarget.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const saved = await api(isNew ? '/api/clients' : `/api/clients/${client.id}`, { method: isNew ? 'POST' : 'PUT', body: JSON.stringify(payload) });
      state.clientEdit = null;
      if (!isNew && state.clientReturnDetail) {
        state.clientReturnDetail = false;
        state.clientDetail = { ...client, ...saved };
        await openClientDetail(state.clientDetail, 'Cliente atualizado.');
      } else {
        state.clientReturnDetail = false;
        state.clientDetail = null;
        await openClients(true, 'Cliente cadastrado.');
      }
    } catch (error) {
      submit.disabled = false;
      updateOnlineUI();
      showBanner(error.message);
    }
  });
}

async function openHistory(reset = false, message = '') {
  if (reset) {
    state.historyParams.offset = 0;
    state.historyItems = [];
  }
  try {
    const params = new URLSearchParams();
    Object.entries(state.historyParams).forEach(([key, value]) => { if (value !== '' && value !== null && value !== undefined) params.set(key, String(value)); });
    const result = await api(`/api/history?${params}`);
    state.historyItems = reset ? result.items || [] : [...state.historyItems, ...(result.items || [])];
    state.historyCount = result.count || 0;
    state.historySummary = result.summary || { items: 0, value: 0, commission: 0 };
    renderHistory(message);
  } catch (error) {
    if (!state.user) return;
    renderHistory();
    showBanner(error.message);
  }
}

function renderHistory(message = '') {
  const channels = state.catalog?.canais || [];
  const channelOptions = '<option value="">Todos os canais</option>' + channels.map((item) =>
    `<option value="${esc(item.id)}" ${String(item.id) === state.historyParams.channel_id ? 'selected' : ''}>${esc(item.nome)}</option>`
  ).join('');
  const periods = [['today', 'Hoje'], ['7days', '7 dias'], ['month', 'Este mês'], ['year', 'Este ano'], ['all', 'Tudo'], ['custom', 'Escolher datas']];
  const periodOptions = periods.map(([value, label]) => `<option value="${value}" ${state.historyParams.period === value ? 'selected' : ''}>${label}</option>`).join('');
  let lastDay = '';
  const itemsMarkup = state.historyItems.length ? state.historyItems.map((sale) => {
    const day = String(sale.data_venda || '').slice(0, 10);
    const heading = day !== lastDay ? `<h3 class="date-heading">${formatDate(day)}</h3>` : '';
    lastDay = day;
    const parts = [sale.canal?.nome, sale.pagamento?.descricao, sale.status?.nome].filter(Boolean).map(esc);
    const discount = Number(sale.valor_desconto || 0);
    return `${heading}<article class="history-card">
      <div class="history-heading"><strong>${esc(sale.cliente || 'Sem cliente')}</strong><strong>${currency(sale.valor_final)}</strong></div>
      <p>${esc(sale.quantidade)} × ${esc(sale.produto?.descricao || 'Produto')} <small>(${esc(sale.produto?.codigo_interno || '—')})</small></p>
      <small>${parts.join(' · ')}${discount ? `${parts.length ? ' · ' : ''}desconto ${currency(discount)}` : ''}</small>
    </article>`;
  }).join('') : '<p class="empty">Nenhuma venda encontrada para estes filtros.</p>';
  const summary = state.historySummary || { items: 0, value: 0, commission: 0 };
  root.innerHTML = shellMarkup('Histórico', 'history', `
    <section class="panel">
      <form id="history-filter">
        <div class="grid-two">
          <div class="field"><label for="history-period">Período</label><select id="history-period">${periodOptions}</select></div>
          <div class="field"><label for="history-channel">Canal</label><select id="history-channel">${channelOptions}</select></div>
        </div>
        <div id="history-custom-dates" class="grid-two ${state.historyParams.period === 'custom' ? '' : 'hidden'}">
          <div class="field"><label for="history-start">De</label><input id="history-start" type="date" value="${esc(state.historyParams.start)}" max="${localISODate()}" /></div>
          <div class="field"><label for="history-end">Até</label><input id="history-end" type="date" value="${esc(state.historyParams.end)}" max="${localISODate()}" /></div>
        </div>
        <div class="field"><label for="history-client">Cliente</label><input id="history-client" placeholder="Buscar por nome" value="${esc(state.historyParams.client)}" /></div>
        <button class="button secondary" type="submit">Aplicar filtros</button>
      </form>
      <div class="summary-grid">
        <div class="summary-tile"><small>Itens vendidos</small><strong>${summary.items}</strong></div>
        <div class="summary-tile"><small>Total vendido</small><strong>${currency(summary.value)}</strong></div>
      </div>
      ${summary.commission ? `<p class="muted commission-note">Comissão no período: ${currency(summary.commission)}</p>` : ''}
      <div id="history-list">${itemsMarkup}</div>
      <div class="pagination ${state.historyItems.length < state.historyCount ? '' : 'hidden'}"><button id="more-history" class="button secondary" type="button">Mostrar mais</button></div>
    </section>`);
  bindShell();
  if (message) showBanner(message, 'success');
  document.querySelector('#history-period').addEventListener('change', (event) => {
    document.querySelector('#history-custom-dates').classList.toggle('hidden', event.target.value !== 'custom');
  });
  document.querySelector('#history-filter').addEventListener('submit', (event) => {
    event.preventDefault();
    state.historyParams = {
      period: document.querySelector('#history-period').value,
      channel_id: document.querySelector('#history-channel').value,
      client: document.querySelector('#history-client').value.trim(),
      start: document.querySelector('#history-start')?.value || '',
      end: document.querySelector('#history-end')?.value || '',
      offset: 0,
    };
    openHistory(true);
  });
  document.querySelector('#more-history')?.addEventListener('click', () => {
    state.historyParams.offset = state.historyItems.length;
    openHistory(false);
  });
}

function currentTheme() {
  return localStorage.getItem('semijoias-theme') || 'auto';
}

function applyTheme(theme) {
  if (theme === 'auto') {
    document.documentElement.removeAttribute('data-theme');
    localStorage.removeItem('semijoias-theme');
  } else {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('semijoias-theme', theme);
  }
}

async function openSettings() {
  try {
    state.appConfig = await api('/api/config');
    renderSettings();
  } catch (error) {
    if (!state.user) return;
    renderSettings();
    showBanner(error.message);
  }
}

function renderSettings() {
  const config = state.appConfig || { environment: 'Não identificado', database_host: '—', app_version: '—' };
  const theme = currentTheme();
  root.innerHTML = shellMarkup('Configurações', 'settings', `
    <section class="panel">
      <h2>Aparência</h2>
      <div class="field"><label for="theme-select">Tema</label><select id="theme-select">
        <option value="auto" ${theme === 'auto' ? 'selected' : ''}>Automático</option>
        <option value="light" ${theme === 'light' ? 'selected' : ''}>Claro</option>
        <option value="dark" ${theme === 'dark' ? 'selected' : ''}>Escuro</option>
      </select></div>
      <p class="muted">Automático acompanha a preferência de aparência do celular.</p>
    </section>
    <section class="panel">
      <h2>Conta</h2>
      <div class="settings-row"><small>Usuário logado</small><strong>${esc(state.user?.email || '—')}</strong></div>
      <button id="settings-logout" class="button primary" type="button">Sair da conta</button>
    </section>
    <section class="panel">
      <h2>Ambiente</h2>
      <div class="settings-row"><small>Rodando em</small><strong>${esc(config.environment)}</strong></div>
      <div class="settings-row"><small>Banco de dados</small><strong>${esc(config.database_host)}</strong></div>
      <div class="settings-row"><small>Versão do deploy</small><strong>v${esc(config.app_version)}</strong></div>
      <div class="settings-row"><small>Aplicativo</small><strong>PWA de vendas</strong></div>
    </section>`);
  bindShell();
  applyTheme(theme);
  document.querySelector('#theme-select').addEventListener('change', (event) => applyTheme(event.target.value));
  document.querySelector('#settings-logout').addEventListener('click', logoutFromPwa);
}

window.addEventListener('online', updateOnlineUI);
window.addEventListener('offline', updateOnlineUI);
window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  state.installing = event;
});

applyTheme(currentTheme());

api('/api/me').then(async (user) => {
  await openSales(user);
}).catch(() => renderLogin(navigator.onLine ? '' : 'Conecte-se à internet para entrar e registrar vendas.'));
