// 自动从 index.html 拆出来的页签模板。改这个文件即可，不用动 index.html。
// 说明：模板最终会被拼进同一个 Vue 实例，所以能直接访问根组件的 data 和 methods。
window.FMP_TPL = window.FMP_TPL || {};
window.FMP_TPL.manage = `
      <el-tab-pane label="知识库管理" name="manage">
        <div class="chart" style="margin-bottom:14px">
          <h3>已接入的知识库插件</h3>
          <el-table :data="plugins" size="small" empty-text="暂无">
            <el-table-column prop="name" label="名称" width="150"></el-table-column>
            <el-table-column prop="slug" label="标识" width="110"></el-table-column>
            <el-table-column label="类型" width="90">
              <template #default="s">
                <el-tag size="small" :type="s.row.kind==='graph'?'warning':'success'">
                  {{ s.row.kind==='graph' ? '图谱' : '向量' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="chunk_count" label="知识块" width="90"></el-table-column>
            <el-table-column prop="protected_count" label="受保护" width="90"></el-table-column>
            <el-table-column prop="uploaded_count" label="上传入库" width="100"></el-table-column>
            <el-table-column prop="call_count" label="调用次数" width="90"></el-table-column>
            <el-table-column prop="corpus_version" label="语料版本" width="150"></el-table-column>
            <el-table-column prop="description" label="用途" min-width="260" show-overflow-tooltip></el-table-column>
          </el-table>
        </div>
        <div class="chart">
          <h3>上传文档入库</h3>
          <el-form label-width="90px" style="max-width:760px">
            <el-form-item label="目标知识库">
              <el-select v-model="ingestSlug" style="width:240px" placeholder="选择插件">
                <el-option v-for="p in vectorPlugins" :key="p.slug"
                           :label="p.name" :value="p.slug"></el-option>
              </el-select>
            </el-form-item>
            <el-form-item label="来源标识">
              <el-input v-model="ingestSource" placeholder="例如：现场公告2026秋"/>
            </el-form-item>
            <el-form-item label="文档内容">
              <el-input v-model="ingestContent" type="textarea" :rows="8"
                        placeholder="支持 Markdown；也支持带 @@@RAG_CHUNK_START@@@ 标记的结构化语料"/>
            </el-form-item>
            <el-form-item>
              <el-button :disabled="!canIngest" @click="doIngest(true)">预览切块</el-button>
              <el-button type="primary" :disabled="!canIngest" :loading="ingesting"
                         @click="doIngest(false)">确认入库</el-button>
            </el-form-item>
          </el-form>
          <el-alert v-if="ingestMsg" :title="ingestMsg" :type="ingestOk?'success':'error'"
                    :closable="false" show-icon></el-alert>
        </div>
      </el-tab-pane>
`;
