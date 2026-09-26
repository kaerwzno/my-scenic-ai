// 管理端逻辑。页签模板在 tabs/*.js 里，index.html 只是外壳。
//
// 拆分方式：模板字符串拼进同一个 Vue 实例 —— 作用域和拆分前完全一样，
// 所以 data / computed / methods 都不用改。
const FMP_TPL = window.FMP_TPL || {};

const HEAD = `
  <div class="topbar">
    <h1>灵山 AI 导游 · 管理端</h1>
    <span class="sub">Fay 内核 · 可插拔知识库</span>
    <button @click="loadAll">刷新数据</button>
    <span class="right">{{ apiBase }} · {{ lastRefresh }}</span>
  </div>

  <div class="wrap">
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" style="margin-bottom:14px"></el-alert>
    <el-alert v-if="softErrors.length" type="warning" show-icon :closable="false" style="margin-bottom:14px"
              :title="'这几块数据没取到，页面显示的是空值：' + softErrors.join('；')"></el-alert>

    <el-tabs v-model="tab" @tab-change="onTabChange">`;

const FOOT = `
    </el-tabs>
  </div>
</div>
`;

const API = 'http://127.0.0.1:5174';
const charts = {};

const { createApp, reactive, ref } = Vue;

// ★ 关键：把模板**写进 #app 的 DOM**，而不是用 createApp 的 template 选项。
//
//   为什么：这个 vue.global.prod.js 是 **runtime-only 构建**（不带模板编译器）——
//   实测它的代码里 `v-else`、`v-for`、`CompileError` 这些编译器特征串一个都没有。
//   用 `template:` 选项传字符串模板，它编不了，页面就白屏（这个坑踩了很久）。
//
//   而走 DOM 内联模板这条路，原版页面一直是能跑的。所以这里保持同样的路子：
//   先把拼好的模板塞进 #app，再 mount。**作用域和拆分前完全一样**。
document.getElementById('app').innerHTML =
  HEAD + FMP_TPL.dash + FMP_TPL.kb + FMP_TPL.graph
       + FMP_TPL.tourism + FMP_TPL.rules + FMP_TPL.manage + FOOT;

createApp({
  data() {
    return {
      apiBase: API,
      tab: 'dash',
      error: '',
      lastRefresh: '',
      overview: { conversation:{total_messages:0,user_messages:0,active_users:0},
                  trend_by_day:[], trend_by_hour:[],
                  kb_calls:{total:0,ok:0,reliable:0,reliable_rate:0,avg_elapsed_ms:0} },
      queries: { hot_queries:[], missed_queries:[], missed_count:0, calls_by_plugin:{} },
      usage: { plugins: [] },
      gaps: { categories: [], gap_count:0, high_gap_count:0, total_queries:0, out_of_domain_queries:0 },
      refusals: { total_pairs:0, refusal_count:0, refusal_rate:0, knowledge_gap_count:0,
                  out_of_domain_count:0, knowledge_gaps:[], out_of_domain:[],
                  note:'域外问题（问天气、写代码）拒答是对的，不用补；知识库确实没有、游客又问到的，才要补。' },
      // 自进化的分析结果（离线 agent 产出，见 docs/knowledge-evolution.json）
      evo: { available:false, gaps:[], redundant:[], conflicts:[], summary:{}, model:'', generated_at:'' },
      entities: { entities: [], count:0, note:'' },
      softErrors: [],
      catConfig: {}, catJson: '', catMsg: '', catOk: true,
      plugins: [],
      tourism: {},
      tourismLoading: false,
      graphStats: null,
      graphMinDegree: 2,
      graphMaxNodes: 300,
      ingestSlug: '', ingestSource: '管理端上传', ingestContent: '',
      ingesting: false, ingestMsg: '', ingestOk: true,
      // 「接受并补充」弹窗：预填问法，管理员只填正文
      fillVisible: false, fillMsg: '', fillOk: true,
      fill: { topic:'', id:'', slug:'kb-lingshan', spot:'', info_type:'',
              questions:'', content:'' },
      rejectVisible: false, rejectMsg: '',
      reject: { section:'', id:'', topic:'', note:'' },
      evoMsg: '',
    };
  },
  computed: {
    vectorPlugins() { return this.plugins.filter(p => p.kind === 'vector'); },
    canIngest() { return this.ingestSlug && this.ingestContent.trim().length > 0; },
    catCategoryCount() { return Object.keys(this.catConfig.categories || {}).length; },
    catRows() {
      const cats = this.catConfig.categories || {};
      return Object.entries(cats).map(([name, spec]) => ({
        name,
        keywords: (spec.keywords || []).join(' / '),
        info_types: (spec.info_types || []).join('、') || '（无 → 天然缺口）',
        note: spec.note || '',
      }));
    },
  },
  methods: {
    pct(v) { return ((v || 0) * 100).toFixed(1) + '%'; },
    async get(path) {
      const r = await fetch(API + path);
      if (!r.ok) throw new Error(path + ' → HTTP ' + r.status);
      return r.json();
    },
    // 可选接口：拿不到就记一笔，别让页面悄悄显示成 0 ——
    // 「拒答分析」曾经因为接口 500 而整块空白，看不出来是没数据还是坏了
    async loadOptional(key, path) {
      try { this[key] = await this.get(path); }
      catch (e) { this.softErrors.push(path + '（' + e.message + '）'); }
    },
    chart(id, option) {
      const el = document.getElementById(id);
      if (!el) return;
      if (!charts[id]) charts[id] = echarts.init(el);
      charts[id].setOption(option, true);
    },

    // 点「接受并补充」：打开弹窗，把问法预填好，管理员只填正文。
    //
    // ★ 这一步是必需的，不能只改状态。之前的实现点了"接受"只把 status 改成
    //   accepted，管理员没有地方填内容——实测被问到"点了接受然后去哪上传？"。
    //   接受 = 受理这个缺口；补充 = 真的写进知识库。两件事必须连起来。
    // 驳回：必须先填理由
    openReject(section, row) {
      this.reject = { section, id: row.id || row.topic || row.scope,
                      topic: row.topic || row.scope || '', note: '' };
      this.rejectMsg = '';
      this.rejectVisible = true;
    },

    async doReject() {
      const rj = this.reject;
      if (!rj.note.trim()) { this.rejectMsg = '请填写驳回理由'; return; }
      const ok = await this.evoSet(rj.section, { id: rj.id }, 'rejected', rj.note);
      if (ok) { this.rejectVisible = false; }
      else { this.rejectMsg = '保存失败，看看页顶的提醒'; }
    },

    openFill(row) {
      const qs = [].concat(row.questions || [], row.ask_variants || []);
      this.fill = {
        topic: row.topic || '',
        id: row.id || row.topic || '',
        slug: row.plugin || 'kb-lingshan',      // agent 会给，没有就默认灵山
        spot: '',                                // 管理员填，如"灵山胜境 / 景区交通"
        info_type: row.topic || '',              // 信息类型默认用主题名
        questions: qs.join('\n'),                // 问法预填（原始 + 扩展）
        content: ''                              // 正文留空，等管理员查证后填
      };
      this.fillMsg = '';
      this.fillVisible = true;
    },

    // 把表单拼成插件能解析的结构化格式（@@@RAG_CHUNK_START@@@ 标记）
    buildChunkText() {
      const f = this.fill;
      const qs = (f.questions || '').split('\n').map(s => s.trim()).filter(Boolean);
      return [
        '@@@RAG_CHUNK_START@@@',
        `景点名称：${f.spot || '灵山胜境'}`,
        `景区：${f.slug === 'kb-nianhuawan' ? '拈花湾禅意小镇' : '灵山胜境'}`,
        `信息类型：${f.info_type || '补充知识'}`,
        ...qs.map(q => `- ${q}`),
        `内容：${(f.content || '').trim()}`,
        '@@@RAG_CHUNK_END@@@',
      ].join('\n');
    },

    async doFill(dryRun) {
      const f = this.fill;
      if (!f.content.trim()) { this.fillOk = false; this.fillMsg = '正文不能为空'; return; }
      this.fillMsg = dryRun ? '预览中…' : '入库中…';
      this.fillOk = true;
      try {
        const r = await fetch(API + '/api/plugins/' + f.slug + '/ingest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: this.buildChunkText(),
                                 source: '自进化补充：' + f.topic,
                                 dry_run: !!dryRun })
        });
        const j = await r.json();
        this.fillOk = !!j.success;
        if (j.success) {
          const res = j.result && j.result.result;
          this.fillMsg = dryRun
            ? '预览切块：' + JSON.stringify(res).slice(0, 300)
            : '入库成功 —— ' + JSON.stringify(res).slice(0, 240)
              + '（下一步：把这条标记为「已补」，并复测原来的问题）';
          if (!dryRun) await this.evoSet('gaps', { id: f.id }, 'done');
          if (!dryRun) await this.refreshAfterFill();
        } else {
          this.fillMsg = j.message || JSON.stringify(j).slice(0, 300);
        }
      } catch (e) {
        this.fillOk = false;
        this.fillMsg = '请求失败：' + e.message;
      }
    },

    // 入库后刷新知识块统计（切块数会变），但不重跑分析（那要调大模型）
    async refreshAfterFill() {
      await this.loadOptional('usage', '/api/stats/kb-usage');
    },

    // 自进化条目的审核。接受/驳回会写回 knowledge-evolution.json，
    // 而且**重跑分析不会覆盖审核状态**（分析脚本会保留 status 字段）。
    // 返回 true/false —— 调用方（比如驳回弹窗）需要知道成没成。
    // note 只有驳回时才传：驳回理由要存下来。
    async evoSet(section, row, status, note) {
      const id = row.id || row.topic || row.scope;
      if (!id) return false;
      try {
        const r = await fetch(API + '/api/evolution/' + section + '/' + encodeURIComponent(id) + '/status', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status, note: note || '' })
        });
        const j = await r.json();
        if (j.ok) {
          row.status = status;
          if (note) row.note = note;
          this.evoMsg = (status === 'accepted' ? '已受理' : status === 'rejected' ? '已驳回' : '已更新')
                        + '：' + (row.topic || row.scope || id);
          return true;
        } else {
          (this.softErrors = this.softErrors || []).push(j.message || '操作失败');
          return false;
        }
      } catch (e) {
        (this.softErrors = this.softErrors || []).push('操作失败：' + e.message);
        return false;
      }
    },
    onTabChange(name) {
      this.$nextTick(() => {
        Object.values(charts).forEach(c => c.resize());
        // 按需加载：切到哪个页签才拉哪个页签的数据（游客行为那个 xlsx 很重，14 万行）
        if (name === 'graph' && !this.graphStats) this.loadGraph();
        if (name === 'tourism' && !this.tourism.total_rows) this.loadTourism().catch(e => { this.error = e.message; });
        if (name === 'manage') this.loadPlugins().catch(() => {});
        if (name === 'rules' && !this.catJson) this.loadCategories();
      });
    },

    async loadDash() {
      this.overview = await this.get('/api/stats/overview');
      const days = this.overview.trend_by_day || [];
      this.chart('c-day', {
        tooltip: { trigger: 'axis' },
        grid: { left: 40, right: 20, top: 24, bottom: 30 },
        xAxis: { type: 'category', data: days.map(d => d.date) },
        yAxis: { type: 'value' },
        series: [{ type: 'line', smooth: true, areaStyle: { opacity: .18 },
                   itemStyle: { color: '#4E327D' }, data: days.map(d => d.count) }],
      });
      const hours = this.overview.trend_by_hour || [];
      this.chart('c-hour', {
        tooltip: { trigger: 'axis' },
        grid: { left: 40, right: 20, top: 24, bottom: 30 },
        xAxis: { type: 'category', data: hours.map(h => h.hour) },
        yAxis: { type: 'value' },
        series: [{ type: 'bar', itemStyle: { color: '#7c5cbf' }, data: hours.map(h => h.count) }],
      });
    },

    async loadKnowledge() {
      this.queries = await this.get('/api/stats/queries');
      this.usage = await this.get('/api/stats/kb-usage');
      await this.loadOptional('gaps', '/api/stats/gaps');
      await this.loadOptional('refusals', '/api/stats/refusals');
      await this.loadOptional('entities', '/api/stats/entities');
      // 自进化的分析结果。它来自离线 agent，可能还没跑过（available=false）
      await this.loadOptional('evo', '/api/evolution');
      const hot = this.queries.hot_queries || [];
      this.chart('c-hot', {
        tooltip: { trigger: 'axis' },
        grid: { left: 150, right: 30, top: 16, bottom: 30 },
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: hot.map(h => h.query).reverse() },
        series: [{ type: 'bar', itemStyle: { color: '#4E327D' },
                   data: hot.map(h => h.count).reverse() }],
      });
      const byPlugin = this.queries.calls_by_plugin || {};
      this.chart('c-calls', {
        tooltip: { trigger: 'item' },
        series: [{ type: 'pie', radius: ['40%','68%'],
                   data: Object.entries(byPlugin).map(([k,v]) => ({ name: k, value: v })),
                   label: { formatter: '{b}\n{c} 次' } }],
      });
    },

    async loadPlugins() {
      const r = await this.get('/api/plugins');
      this.plugins = r.plugins || [];
      if (!this.ingestSlug && this.vectorPlugins.length) this.ingestSlug = this.vectorPlugins[0].slug;
    },

    async loadCategories() {
      this.catMsg = '';
      try {
        const r = await fetch(API + '/api/categories');
        const j = await r.json();
        // 去掉下划线开头的说明字段，编辑区只留真正要改的部分
        const clean = {};
        Object.entries(j).forEach(([k, v]) => {
          if (k === '_file' || k === '_exists') { clean[k] = v; return; }
          if (k.startsWith('_')) return;
          clean[k] = v;
        });
        this.catConfig = j;
        this.catJson = JSON.stringify(clean, null, 2);
      } catch (e) { this.catOk = false; this.catMsg = '载入失败：' + e.message; }
    },

    async saveCategories() {
      this.catMsg = '';
      let payload;
      try { payload = JSON.parse(this.catJson); }
      catch (e) { this.catOk = false; this.catMsg = 'JSON 格式错误：' + e.message; return; }
      try {
        const r = await fetch(API + '/api/categories', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const j = await r.json();
        this.catOk = !!j.success;
        this.catMsg = j.success ? `已保存：${j.categories.length} 个类别，立即生效` : (j.message || '保存失败');
        if (j.success) { await this.loadCategories(); await this.loadKnowledge(); }
      } catch (e) { this.catOk = false; this.catMsg = '保存失败：' + e.message; }
    },

    async loadTourism() {
      this.tourismLoading = true;
      try {
        this.tourism = await this.get('/api/stats/tourism');
      } finally {
        this.tourismLoading = false;
      }
      const t = this.tourism;
      if (!t || t.error) return;
      const pie = (id, data, title) => this.chart(id, {
        tooltip: { trigger: 'item' },
        series: [{ type: 'pie', radius: ['38%','66%'], data,
                   label: { formatter: p => p.name + '\n' + (p.percent||0).toFixed(1) + '%' } }],
      });
      pie('c-type', t.attraction_type);
      const spots = (t.top_attractions || []).slice(0, 12);
      this.chart('c-spot', {
        tooltip: { trigger: 'axis' },
        grid: { left: 120, right: 30, top: 16, bottom: 30 },
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: spots.map(s => s.name).reverse() },
        series: [{ type: 'bar', itemStyle: { color: '#B87830' },
                   data: spots.map(s => s.value).reverse() }],
      });
      const bar = (id, data, color) => this.chart(id, {
        tooltip: { trigger: 'axis' },
        grid: { left: 60, right: 24, top: 24, bottom: 46 },
        xAxis: { type: 'category', data: (data||[]).map(d => d.name), axisLabel: { rotate: 30 } },
        yAxis: { type: 'value' },
        series: [{ type: 'bar', itemStyle: { color }, data: (data||[]).map(d => d.value) }],
      });
      bar('c-age', t.age_buckets, '#7c5cbf');
      bar('c-stay', t.stay_buckets, '#4E9E8F');
      bar('c-ticket', t.ticket_buckets, '#C1604C');
      bar('c-month', t.by_month, '#5B7BB4');
    },

    async loadGraph() {
      const r = await this.get(`/api/graph/extract?min_degree=${this.graphMinDegree}&max_nodes=${this.graphMaxNodes}`);
      if (r.error) { this.error = r.error; return; }
      this.graphStats = r.stats;
      const cats = (r.categories || []).map(c => c.name);
      this.$nextTick(() => {
        const el = document.getElementById('graph');
        if (!el) return;
        if (!charts.graph) charts.graph = echarts.init(el);
        charts.graph.setOption({
          tooltip: { formatter: p => p.dataType === 'node'
              ? `<b>${p.data.name}</b><br/>类型：${p.data.category}<br/>${(p.data.description||'').slice(0,120)}`
              : `${p.data.source} → ${p.data.target}<br/>${p.data.name||''}` },
          legend: [{ data: cats, top: 0, type: 'scroll' }],
          series: [{
            type: 'graph', layout: 'force', roam: true, draggable: true,
            categories: cats.map(c => ({ name: c })),
            data: r.nodes, links: r.links,
            force: { repulsion: 90, edgeLength: [40, 130], gravity: .08 },
            label: { show: true, fontSize: 10, formatter: p => p.data.name },
            lineStyle: { color: '#c9c1b5', width: .7, curveness: .08 },
            emphasis: { focus: 'adjacency', lineStyle: { width: 2 } },
          }],
        }, true);
      });
    },

    async doIngest(dryRun) {
      this.ingesting = true; this.ingestMsg = '';
      try {
        const r = await fetch(`${API}/api/plugins/${this.ingestSlug}/ingest`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: this.ingestContent, source: this.ingestSource, dry_run: dryRun }),
        });
        const j = await r.json();
        this.ingestOk = !!j.success;
        if (j.success) {
          const res = j.result && j.result.result;
          this.ingestMsg = `已调用 ${j.server}：${JSON.stringify(res).slice(0, 300)}`;
          if (!dryRun) { await this.loadPlugins(); }
        } else {
          this.ingestMsg = j.message || JSON.stringify(j).slice(0, 300);
        }
      } catch (e) {
        this.ingestOk = false; this.ingestMsg = '请求失败：' + e.message;
      } finally { this.ingesting = false; }
    },

    async loadAll() {
      this.error = '';
      this.softErrors = [];
      try {
        await this.loadPlugins();
        await this.loadDash();
        await this.loadKnowledge();
      } catch (e) { this.error = '加载失败：' + e.message + '（确认 admin-api 已在 5174 运行）'; }
      this.lastRefresh = new Date().toLocaleTimeString();
    },
  },
  mounted() {
    this.loadAll();
    window.addEventListener('resize', () => Object.values(charts).forEach(c => c.resize()));
  },
}).use(ElementPlus).mount('#app');
