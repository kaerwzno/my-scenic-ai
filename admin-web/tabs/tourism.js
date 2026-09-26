// 自动从 index.html 拆出来的页签模板。改这个文件即可，不用动 index.html。
// 说明：模板最终会被拼进同一个 Vue 实例，所以能直接访问根组件的 data 和 methods。
window.FMP_TPL = window.FMP_TPL || {};
window.FMP_TPL.tourism = `
      <el-tab-pane label="游客行为分析" name="tourism">
        <div class="note" v-if="tourism.disclaimer">⚠️ {{ tourism.disclaimer }}</div>
        <div class="note" v-if="tourismLoading">
          正在解析 14 万行 xlsx，第一次打开要十几秒，之后就缓存了 —— 页面没反应就是在等这个。
        </div>
        <div class="cards" v-if="tourism.total_rows">
          <div class="card"><div class="label">数据行数</div>
            <div class="value">{{ tourism.total_rows.toLocaleString() }}<small>行</small></div></div>
          <div class="card"><div class="label">数据源</div>
            <div class="value" style="font-size:14px">{{ tourism.source }}</div></div>
        </div>
        <div class="grid2">
          <div class="chart"><h3>景点类型分布</h3><div id="c-type" style="height:320px"></div></div>
          <div class="chart"><h3>热门景点 TOP 12</h3><div id="c-spot" style="height:320px"></div></div>
          <div class="chart"><h3>年龄分布</h3><div id="c-age" style="height:300px"></div></div>
          <div class="chart"><h3>停留时长分布</h3><div id="c-stay" style="height:300px"></div></div>
          <div class="chart"><h3>票价分布</h3><div id="c-ticket" style="height:300px"></div></div>
          <div class="chart"><h3>到访月份分布</h3><div id="c-month" style="height:300px"></div></div>
        </div>
      </el-tab-pane>
`;
