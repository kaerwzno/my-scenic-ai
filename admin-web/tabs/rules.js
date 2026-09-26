// 自动从 index.html 拆出来的页签模板。改这个文件即可，不用动 index.html。
// 说明：模板最终会被拼进同一个 Vue 实例，所以能直接访问根组件的 data 和 methods。
window.FMP_TPL = window.FMP_TPL || {};
window.FMP_TPL.rules = `
      <el-tab-pane label="分类规则" name="rules">
        <div class="note">
          <b>问题类别规则（可编辑）</b> —— 这一页决定"知识缺口分析"怎么归类。
          它属于领域知识（灵山游客会问什么），换景区时需要替换。
          <br><br>
          <b>关于天气这类交叉问题</b>：判据是"在问实时状况，还是在问游玩建议"，不看有没有天气两个字。
          判定顺序：<strong>① 先看有没有景区意图（怎么玩、怎么安排、适合…）；
          ② 再看"近期时间词 + 天气词"是否同时出现；③ 最后看气象参数词。</strong>
        </div>
        <div class="grid2">
          <div class="chart">
            <h3>当前类别（{{ catCategoryCount }} 个）</h3>
            <div class="muted" style="margin:-6px 0 8px">规则文件：{{ catConfig._file }}</div>
            <el-table :data="catRows" size="small" max-height="460">
              <el-table-column prop="name" label="类别" width="110"></el-table-column>
              <el-table-column prop="keywords" label="关键词" min-width="240" show-overflow-tooltip></el-table-column>
              <el-table-column prop="info_types" label="对应知识类型" width="140"></el-table-column>
              <el-table-column prop="note" label="说明" min-width="160" show-overflow-tooltip></el-table-column>
            </el-table>
          </div>
          <div class="chart">
            <h3>编辑规则（JSON）</h3>
            <div class="muted" style="margin:-6px 0 8px">
              改完点保存，<strong>立即生效</strong>，不用重启。
            </div>
            <el-input v-model="catJson" type="textarea" :rows="20" class="mono"></el-input>
            <div style="margin-top:10px">
              <el-button @click="loadCategories">重新载入</el-button>
              <el-button type="primary" @click="saveCategories">保存规则</el-button>
            </div>
            <el-alert v-if="catMsg" :title="catMsg" :type="catOk?'success':'error'"
                      :closable="false" show-icon style="margin-top:10px"></el-alert>
          </div>
        </div>
      </el-tab-pane>
`;
