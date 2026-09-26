// 自动从 index.html 拆出来的页签模板。改这个文件即可，不用动 index.html。
// 说明：模板最终会被拼进同一个 Vue 实例，所以能直接访问根组件的 data 和 methods。
window.FMP_TPL = window.FMP_TPL || {};
window.FMP_TPL.dash = `
      <el-tab-pane label="数据看板" name="dash">
        <div class="cards">
          <div class="card"><div class="label">累计对话消息</div>
            <div class="value">{{ overview.conversation.total_messages }}<small>条</small></div></div>
          <div class="card"><div class="label">游客提问</div>
            <div class="value">{{ overview.conversation.user_messages }}<small>条</small></div></div>
          <div class="card"><div class="label">活跃用户</div>
            <div class="value">{{ overview.conversation.active_users }}<small>人</small></div></div>
          <div class="card"><div class="label">知识库调用</div>
            <div class="value">{{ overview.kb_calls.total }}<small>次</small></div></div>
          <div class="card"><div class="label">知识库命中率</div>
            <div class="value">{{ pct(overview.kb_calls.reliable_rate) }}<small></small></div></div>
          <div class="card">
            <div class="label">检索耗时 · 中位（不含大模型）</div>
            <div class="value">{{ overview.kb_calls.elapsed_median_ms }}<small>ms</small></div>
            <div class="foot">
              冷启动 {{ overview.kb_calls.slow_count }} 次（含加载嵌入模型，可达数秒）<br>
              去掉冷启动后中位 {{ overview.kb_calls.elapsed_warm_median_ms }} ms
            </div>
          </div>
          <div class="card">
            <div class="label">端到端响应 · 中位（含大模型）</div>
            <div class="value">
              <template v-if="overview.latency && overview.latency.available">
                {{ overview.latency.final_median_s }}<small>秒</small>
              </template>
              <template v-else>—</template>
            </div>
            <div class="foot">
              <template v-if="overview.latency && overview.latency.available">
                首字 {{ overview.latency.first_median_s }} 秒 ｜ 样本 {{ overview.latency.sample }} 次<br>
                来自 measure_latency.py 的测量
              </template>
              <template v-else>还没测：跑 scripts/measure_latency.py</template>
            </div>
          </div>
        </div>
        <div class="grid2">
          <div class="chart"><h3>对话量趋势（按天）</h3><div id="c-day" style="height:280px"></div></div>
          <div class="chart"><h3>活跃时段分布（按小时）</h3><div id="c-hour" style="height:280px"></div></div>
        </div>
      </el-tab-pane>
`;
