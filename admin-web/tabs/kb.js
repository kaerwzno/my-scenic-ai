// 自动从 index.html 拆出来的页签模板。改这个文件即可，不用动 index.html。
// 说明：模板最终会被拼进同一个 Vue 实例，所以能直接访问根组件的 data 和 methods。
window.FMP_TPL = window.FMP_TPL || {};
window.FMP_TPL.kb = `
      <el-tab-pane label="知识分析" name="kb">
        <div class="note">
          <b>本页是「知识库自进化」的操作台。</b>四块对应四件不同的事：<br>
          <b>① 要补什么</b> —— 数字人答不上来时会申报，这里按主题汇总。
          每条都附<b>原始问题</b>和<b>扩展问法</b>，你可以判断是不是真缺口。<br>
          <b>② 要降什么</b> —— 知识块多但几乎没人问的内容。<br>
          <b>③ 回答质量</b> —— 这不是缺口判据，是诊断：哪一类的检索匹配得不够准。
          命中率 = 铁证率（相似度足够高），弱相关不计入。<br>
          <b>④ 系统故障</b> —— 报错独立一栏，不混进缺口。
          <div style="margin-top:8px" class="muted">
            分析数据由离线 agent（{{ evo.model || 'deepseek-v4-flash' }}）生成，
            时间 {{ evo.generated_at || '（还没跑过）' }}。
            重跑命令：<code>python scripts/analyze_knowledge.py</code>
          </div>
        </div>

        <!-- ① 要补什么 -->
        <el-alert v-if="evoMsg" :title="evoMsg" type="success" :closable="true"
                  show-icon style="margin-bottom:10px" @close="evoMsg=''"></el-alert>
        <div class="chart" style="margin-bottom:14px">
          <h3 style="display:flex;align-items:center;gap:10px">
            ① 要补什么 —— 按主题汇总的知识缺口
            <el-tag v-if="evo.gaps && evo.gaps.length" type="danger" size="small">
              {{ evo.gaps.length }} 个主题
            </el-tag>
            <el-tag v-else type="success" size="small">暂无缺口申报</el-tag>
            <span class="muted">来源：{{ evo.summary ? evo.summary.unknowns : 0 }} 条申报</span>
          </h3>
          <el-table :data="evo.gaps || []" size="small" empty-text="还没有申报记录 —— 数字人答不上来时会自动记一笔" row-key="topic">
            <el-table-column type="expand">
              <template #default="s">
                <div style="padding:6px 18px 12px;line-height:1.8">
                  <div><b>原始问题</b>（游客实际怎么问的）：</div>
                  <div v-for="(q, i) in s.row.questions" :key="i" class="muted">· {{ q }}</div>
                  <div style="margin-top:8px"><b>扩展问法</b>（会写进知识块参与检索匹配）：</div>
                  <div class="muted">{{ (s.row.ask_variants || []).join(' ／ ') || '（无）' }}</div>
                  <div style="margin-top:8px"><b>补充建议</b>：{{ s.row.suggestion }}</div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="topic" label="主题" width="130"></el-table-column>
            <el-table-column label="问题数" width="80">
              <template #default="s">{{ (s.row.questions || []).length }}</template>
            </el-table-column>
            <el-table-column label="扩展问法" width="90">
              <template #default="s">
                <span class="muted">{{ (s.row.ask_variants || []).length }} 条</span>
              </template>
            </el-table-column>
            <el-table-column prop="suggestion" label="补充建议（点开行看原始问题）" min-width="300"></el-table-column>
            <el-table-column label="优先级" width="90">
              <template #default="s">
                <el-tag size="small" v-if="s.row.priority==='high'" type="danger">高</el-tag>
                <el-tag size="small" v-else-if="s.row.priority==='medium'" type="warning">中</el-tag>
                <el-tag size="small" v-else type="info">低</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="190">
              <template #default="s">
                <el-tag size="small" v-if="s.row.status==='accepted'" type="warning">已受理</el-tag>
                <el-tag size="small" v-else-if="s.row.status==='done'" type="success">已补</el-tag>
                <el-tag size="small" v-else-if="s.row.status==='rejected'" type="info">
                  已驳回<span v-if="s.row.note" :title="s.row.note"> · 有理由</span>
                </el-tag>
                <span v-else>
                  <el-button size="small" type="primary" @click="openFill(s.row)">接受并补充</el-button>
                  <el-button size="small" @click="openReject('gaps', s.row)">驳回</el-button>
                </span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- 接受之后：补充内容的弹窗。
             这一步是上一步漏掉的 —— 点「接受」只改了状态，没有让管理员填内容，
             结果就是"点了接受然后不知道去哪上传"。 -->
        <el-dialog v-model="fillVisible" title="补充知识" width="720px">
          <div class="muted" style="margin-bottom:10px;line-height:1.7">
            主题「{{ fill.topic }}」。<b>问法已经自动填好了</b>（来自真实游客提问 + agent 扩展），
            你只需要填<b>正文内容</b>。提交后立即写入知识库并参与检索。
          </div>
          <el-form label-width="88px" size="small">
            <el-form-item label="知识库">
              <el-select v-model="fill.slug" style="width:220px">
                <el-option label="灵山胜境" value="kb-lingshan"></el-option>
                <el-option label="拈花湾" value="kb-nianhuawan"></el-option>
              </el-select>
            </el-form-item>
            <el-form-item label="景点名称">
              <el-input v-model="fill.spot" placeholder="如：灵山胜境 / 景区交通"></el-input>
            </el-form-item>
            <el-form-item label="信息类型">
              <el-input v-model="fill.info_type" placeholder="如：交通停车"></el-input>
            </el-form-item>
            <el-form-item label="问题示例">
              <el-input v-model="fill.questions" type="textarea" :rows="4"
                        placeholder="每行一条。已自动填入申报的原始问题和扩展问法"></el-input>
              <div class="muted">这些问法会参与向量嵌入 —— <b>是命中率的关键</b>，不要删。</div>
            </el-form-item>
            <el-form-item label="正文内容">
              <el-input v-model="fill.content" type="textarea" :rows="5"
                        placeholder="填事实即可，如：停车场位于景区东门，小型车 10 元/次…"></el-input>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="fillVisible=false">取消</el-button>
            <el-button @click="doFill(true)">预览切块</el-button>
            <el-button type="primary" :disabled="!fill.content.trim()" @click="doFill(false)">确认入库</el-button>
          </template>
          <el-alert v-if="fillMsg" :title="fillMsg" :type="fillOk?'success':'error'"
                    :closable="false" show-icon style="margin-top:8px"></el-alert>
        </el-dialog>

        <!-- 驳回理由弹窗。驳回必须写理由，原因有两个：
             ① 分析 agent 下次还会提出同一条 —— 有理由才知道是"提错了"还是"暂时不补"；
             ② 隔两周回头看，没人记得当初为什么驳。 -->
        <el-dialog v-model="rejectVisible" title="驳回理由" width="560px">
          <div class="muted" style="margin-bottom:10px;line-height:1.7">
            正在驳回：<b>{{ reject.topic }}</b><br>
            这个理由会保存下来，下次分析 agent 再提出同一条时，你能看到当初为什么驳回。
          </div>
          <el-input v-model="reject.note" type="textarea" :rows="4"
                    placeholder="例如：这个信息属于景区运营数据，资料包里没有，已联系景区但还没回复 / 这条不缺，是检索没配上"></el-input>
          <el-alert v-if="rejectMsg" :title="rejectMsg" type="error" :closable="false"
                    show-icon style="margin-top:8px"></el-alert>
          <template #footer>
            <el-button @click="rejectVisible=false">取消</el-button>
            <el-button type="primary" :disabled="!reject.note.trim()" @click="doReject()">
              确认驳回
            </el-button>
          </template>
        </el-dialog>

        <!-- ② 要降什么 -->
        <div class="chart" style="margin-bottom:14px" v-if="evo.redundant && evo.redundant.length">
          <h3>② 要降什么 —— 知识块多但几乎没人问</h3>
          <el-table :data="evo.redundant" size="small" max-height="240">
            <el-table-column prop="scope" label="范围" width="200"></el-table-column>
            <el-table-column prop="kb_chunks" label="知识块" width="90"></el-table-column>
            <el-table-column prop="suggestion" label="建议" min-width="320"></el-table-column>
            <el-table-column label="操作" width="170">
              <template #default="s">
                <el-tag size="small" v-if="s.row.status==='accepted'" type="warning">已受理</el-tag>
                <el-tag size="small" v-else-if="s.row.status==='done'" type="success">已处理</el-tag>
                <el-tag size="small" v-else-if="s.row.status==='rejected'" type="info">
                  已驳回<span v-if="s.row.note" :title="s.row.note"> · 有理由</span>
                </el-tag>
                <span v-else>
                  <el-button size="small" @click="evoSet('redundant', s.row, 'accepted')">接受</el-button>
                  <el-button size="small" @click="openReject('redundant', s.row)">驳回</el-button>
                </span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- ③ 回答质量（原「知识缺口分析」，改名因为它的用途是诊断检索，不是判缺口）-->
        <div class="chart" style="margin-bottom:14px">
          <h3 style="display:flex;align-items:center;gap:10px">
            ③ 回答质量 —— 哪一类答得不够稳
            <span class="muted">共 {{ gaps.total_queries }} 次查询</span>
          </h3>
          <div class="muted" style="margin:-4px 0 10px;line-height:1.7">
            <b>绿 ≥80%</b> ／ <b>黄 60–80%</b> ／
            <b>红 &lt;60% 且知识库里确实没有这一类</b> ／ 灰 样本不足（少于 3 次不下结论）。<br>
            黄和红的分界<b>不看命中率、看有没有知识支撑</b>——因为该采取的行动不同：
            黄是「知识在，检索没找准」，红是「真缺内容，要喂资料」。
          </div>
          <el-table :data="gaps.categories" size="small" empty-text="还没有查询记录">
            <el-table-column prop="category" label="问题类别" width="110"></el-table-column>
            <el-table-column prop="query_count" label="被问" width="70"></el-table-column>
            <el-table-column label="命中率" width="90">
              <template #default="s">
                <span :style="{color: s.row.hit_rate < 0.6 ? '#C1604C' : (s.row.hit_rate < 0.8 ? '#b8860b' : '#3e8e5a')}">
                  {{ pct(s.row.hit_rate) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="weak_count" label="弱相关" width="80"></el-table-column>
            <el-table-column prop="kb_chunks" label="知识块" width="80"></el-table-column>
            <el-table-column label="级别" width="90">
              <template #default="s">
                <el-tag size="small" v-if="s.row.gap_level==='high'" type="danger">严重</el-tag>
                <el-tag size="small" v-else-if="s.row.gap_level==='medium'" type="warning">待完善</el-tag>
                <el-tag size="small" v-else-if="s.row.gap_level==='ok'" type="success">充足</el-tag>
                <el-tag size="small" v-else type="info">样本不足</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="advice" label="诊断" min-width="330"></el-table-column>
          </el-table>
        </div>

        <!-- ④ 系统故障（条件显示）-->
        <div class="chart" style="margin-bottom:14px" v-if="queries.error_count">
          <h3 style="color:#C1604C">④ 系统故障（{{ queries.error_count }} 次）—— 不是知识缺口，是故障</h3>
          <el-table :data="queries.error_calls" size="small" max-height="200">
            <el-table-column prop="query" label="问题" min-width="240"></el-table-column>
            <el-table-column prop="plugin" label="插件" width="120"></el-table-column>
            <el-table-column prop="error" label="错误" min-width="260"></el-table-column>
            <el-table-column prop="ts" label="时间" width="180"></el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
`;
