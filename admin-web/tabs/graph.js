// 自动从 index.html 拆出来的页签模板。改这个文件即可，不用动 index.html。
// 说明：模板最终会被拼进同一个 Vue 实例，所以能直接访问根组件的 data 和 methods。
window.FMP_TPL = window.FMP_TPL || {};
window.FMP_TPL.graph = `
      <el-tab-pane label="知识图谱" name="graph">
        <div class="graph-box">
          <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:10px">
            <span class="muted">最小度数</span>
            <el-input-number v-model="graphMinDegree" :min="0" :max="20" size="small" @change="loadGraph"></el-input-number>
            <span class="muted">最大节点</span>
            <el-input-number v-model="graphMaxNodes" :min="20" :max="628" :step="50" size="small" @change="loadGraph"></el-input-number>
            <el-button type="primary" size="small" @click="loadGraph">刷新图谱</el-button>
            <span class="muted" v-if="graphStats">
              显示 {{ graphStats.shown_nodes }} 节点 / {{ graphStats.shown_links }} 条边
              （全图 {{ graphStats.total_nodes }} 节点 / {{ graphStats.total_links }} 条边）
            </span>
          </div>
          <div id="graph"></div>
        </div>
      </el-tab-pane>
`;
