<template>
  <el-drawer :visible.sync="dialogVisible" direction="rtl" size="50%" :wrapperClosable="false" :withHeader="false">
    <!-- 自定义标题区域 -->
    <div class="custom-header">
      <div class="header-left">
        <h3 class="bold-title">功能管理</h3>
      </div>
      <button class="custom-close-btn" @click="closeDialog">×</button>
    </div>

    <div class="function-manager">
      <!-- 左侧：未选功能 -->
      <div class="function-column">
        <div class="column-header">
          <h4 class="column-title">未选功能</h4>
          <el-button type="text" @click="selectAll" class="select-all-btn">全选</el-button>
        </div>
        <div class="function-list">
          <div v-for="func in unselected" :key="func.name" class="function-item">
            <el-checkbox :label="func.name" v-model="selectedNames" @change="(val) => handleCheckboxChange(func, val)" @click.native.stop></el-checkbox>
            <div class="func-tag" @click="handleFunctionClick(func)">
              <div class="color-dot" :style="{backgroundColor: getFunctionColor(func.name)}"></div>
              <span>{{ func.name }}</span>
            </div>
            <el-tooltip class="item" effect="dark" :content="func.description || '暂无功能描述'" placement="top">
              <img src="@/assets/home/info.png" alt="" class="info-icon">
            </el-tooltip>
          </div>
        </div>
      </div>

      <!-- 中间：已选功能 -->
      <div class="function-column">
        <div class="column-header">
          <h4 class="column-title">已选功能</h4>
          <el-button type="text" @click="deselectAll" class="select-all-btn">全选</el-button>
        </div>
        <div class="function-list">
          <div v-for="func in selectedList" :key="func.name" class="function-item">
            <el-checkbox :label="func.name" v-model="selectedNames" @change="(val) => handleCheckboxChange(func, val)" @click.native.stop></el-checkbox>
            <div class="func-tag" @click="handleFunctionClick(func)">
              <div class="color-dot" :style="{backgroundColor: getFunctionColor(func.name)}"></div>
              <span>{{ func.name }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：参数配置 -->
      <div class="params-column">
        <h4 v-if="currentFunction" class="column-title">参数配置 - {{ currentFunction.name }}</h4>
          <div v-if="currentFunction" class="params-container">
            <el-form :model="currentFunction" size="mini" class="param-form" v-loading="loading" element-loading-text="拼命加载中" element-loading-spinner="el-icon-loading" element-loading-background="rgba(255, 255, 255, 0.7)">
              <el-form-item v-for="(value, key) in currentFunction.params" :key="key" :label="key" class="param-item">
                <el-input v-model="currentFunction.params[key]" size="mini" class="param-input" @change="(val) => handleParamChange(currentFunction, key, val)"/>
              </el-form-item>
            </el-form>
          </div>
        <div v-else class="empty-tip">请选择已配置的功能进行参数设置</div>
      </div>
    </div>

    <!-- MCP区域 -->
    <div class="mcp-access-point">
      <div class="mcp-container">
        <!-- 左侧区域 -->
        <div class="mcp-left">
          <div class="mcp-header">
            <h3 class="bold-title">MCP接入点</h3>
          </div>
          <div class="url-header">
            <div class="address-desc">
              <span>以下是智能体的MCP接入点地址。</span>
              <a href="https://github.com/xinnan-tech/xiaozhi-esp32-server/blob/main/docs/mcp-endpoint-enable.md"
                target="_blank" class="doc-link">如何部署MCP接入点</a> &nbsp;&nbsp;|&nbsp;&nbsp;
              <a href="https://github.com/xinnan-tech/xiaozhi-esp32-server/blob/main/docs/mcp-endpoint-integration.md"
                target="_blank" class="doc-link">如何接入MCP功能</a> &nbsp;
            </div>
          </div>
          <el-input v-model="mcpUrl" readonly class="url-input">
            <template #suffix>
              <el-button @click="copyUrl" class="inner-copy-btn" icon="el-icon-document-copy">
                复制
              </el-button>
            </template>
          </el-input>
        </div>

        <!-- 右侧区域 -->
        <div class="mcp-right">
          <div class="mcp-header">
            <h3 class="bold-title">接入点状态</h3>
          </div>
          <div class="status-container">
            <span class="status-indicator" :class="mcpStatus"></span>
            <span class="status-text">{{
              mcpStatus === 'connected' ? '已连接' :
                mcpStatus === 'loading' ? '加载中...' : '未连接'
            }}</span>
            <button class="refresh-btn" @click="refreshStatus">
              <span class="refresh-icon">↻</span>
              <span>刷新</span>
            </button>
          </div>
          <div class="mcp-tools-list">
            <div v-if="mcpTools.length > 0" class="tools-grid">
              <el-button v-for="tool in mcpTools" :key="tool" size="small" class="tool-btn" plain>
                {{ tool }}
              </el-button>
            </div>
            <div v-else class="no-tools">
              <span>暂无可用工具</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="drawer-footer">
      <el-button @click="closeDialog">取消</el-button>
      <el-button type="primary" @click="saveSelection">保存配置</el-button>
    </div>
  </el-drawer>
</template>

<script>
export default {
  props: {
    value: Boolean,
    functions: {
      type: Array,
      default: () => []
    }
  },
  data() {
    return {
      dialogVisible: this.value,
      selectedNames: [],
      currentFunction: null,
      modifiedFunctions: {},
      allFunctions: [
        {name: '天气', params: {city: '北京'}, description: '查看指定城市的天气情况'},
        {name: '新闻', params: {type: '科技'}, description: '获取最新科技类新闻资讯'},
        {name: '工具', params: {category: '常用'}, description: '提供常用工具集合'},
        {name: '退出', params: {}, description: '退出当前系统'},
        {name: '音乐', params: {genre: '流行'}, description: '播放流行音乐'},
        {name: '翻译', params: {from: '中文', to: '英文'}, description: '提供中英文互译功能'},
        {name: '计算', params: {precision: '2'}, description: '提供精确计算功能'},
        {name: '日历', params: {view: '月'}, description: '查看月历视图'}
      ],
      functionColorMap: [
        '#FF6B6B', '#4ECDC4', '#45B7D1',
        '#96CEB4', '#FFEEAD', '#D4A5A5', '#A2836E'
      ],
      tempFunctions: {},
      // 添加一个标志位来跟踪是否已经保存
      hasSaved: false,
      loading: false,
    }
  },
  computed: {
    selectedList() {
      return this.allFunctions.filter(f => this.selectedNames.includes(f.name));
    },
    unselected() {
      return this.allFunctions.filter(f => !this.selectedNames.includes(f.name));
    }
  },
  watch: {
    value(newVal) {
      this.dialogVisible = newVal;
      if (newVal) {
        this.selectedNames = this.functions.map(f => f.name);
        this.currentFunction = this.selectedList[0] || null;
      }
    },
    dialogVisible(newVal) {
      this.$emit('input', newVal);
    }
  },
  methods: {
    handleFunctionClick(func) {
      if (this.selectedNames.includes(func.name)) {
        this.loading = true;
        setTimeout(() => {
          const tempFunc = this.tempFunctions[func.name];
          this.currentFunction = tempFunc ? tempFunc : JSON.parse(JSON.stringify(func));
          this.loading = false;
        }, 300);
      }
    },
    handleParamChange(func, key, value) {
      if (!this.tempFunctions[func.name]) {
        this.tempFunctions[func.name] = JSON.parse(JSON.stringify(func));
      }
      this.tempFunctions[func.name].params[key] = value;
    },
    handleCheckboxChange(func, checked) {
      if (checked) {
        if (!this.selectedNames.includes(func.name)) {
          this.selectedNames = [...this.selectedNames, func.name];
        }
      } else {
        this.selectedNames = this.selectedNames.filter(name => name !== func.name);
      }

      if (this.selectedList.length > 0) {
        this.currentFunction = this.selectedList[0];
      } else {
        this.currentFunction = null;
      }
    },

    selectAll() {
      this.selectedNames = [...this.allFunctions.map(f => f.name)];
      if (this.selectedList.length > 0) {
        this.currentFunction = JSON.parse(JSON.stringify(this.selectedList[0]));
      }
    },

    deselectAll() {
      this.selectedNames = [];
      this.currentFunction = null;
    },

    closeDialog() {
      this.tempFunctions = {};
      this.selectedNames = this.functions.map(f => f.name);
      this.currentFunction = null;
      this.dialogVisible = false;
      this.$emit('input', false);
      this.$emit('dialog-closed', false);
    },

    saveSelection() {
      Object.keys(this.tempFunctions).forEach(name => {
        this.modifiedFunctions[name] = JSON.parse(JSON.stringify(this.tempFunctions[name]));
      });
      this.tempFunctions = {};
      this.hasSaved = true;

      const selected = this.selectedList.map(f => {
        const modified = this.modifiedFunctions[f.name];
        return modified || f;
      }).map(f => ({
        ...f,
        params: JSON.parse(JSON.stringify(f.params))
      }));

      this.$emit('update-functions', selected);
      this.dialogVisible = false;
      this.$message.success('配置保存成功');
      // 通知父组件对话框已关闭且已保存
      this.$emit('dialog-closed', true);
    },

    getFunctionColor(name) {
      const hash = [...name].reduce((acc, char) => acc + char.charCodeAt(0), 0);
      return this.functionColorMap[hash % 7];
    }
  }
}
</script>

<style lang="scss" scoped>
.function-manager {
  display: grid;
  grid-template-columns: minmax(120px, 0.5fr) minmax(120px, 0.5fr) minmax(200px, 2fr);
  gap: 12px;
  height: calc(70vh - 60px);
}

.custom-header {
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid #EBEEF5;

  .header-left {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .bold-title {
    font-size: 18px;
    font-weight: bold;
    margin: 0;
  }

  .select-all-btn {
    padding: 0;
    height: auto;
    font-size: 14px;
  }
}

.function-column {
  position: relative;
  width: auto;
  padding: 10px;
  overflow-y: auto;
  border-right: 1px solid #EBEEF5;
  scrollbar-width: none;
}

.function-column::-webkit-scrollbar {
  display: none;
}

.function-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.function-item {
  padding: 8px 12px;
  margin: 4px 0;
  width: 100%;
  text-align: left;
  cursor: pointer;
  border-radius: 4px;
  transition: background-color 0.2s;
  display: flex;
  align-items: center;
  justify-content: space-between;

  &:hover {
    background-color: #f5f7fa;
  }
}

.params-column {
  min-width: 280px;
  padding: 10px;
  overflow-y: auto;
  scrollbar-width: none;
}

.params-column::-webkit-scrollbar {
  display: none;
}

.column-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.column-title {
  text-align: center;
  width: 100%;
}

.func-tag {
  display: flex;
  align-items: center;
  cursor: pointer;
  flex-grow: 1;
  margin-left: 8px;
}

.color-dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  margin-right: 8px;
  border-radius: 50%;
}

.param-form {
  ::v-deep .el-form-item {
    display: flex;
    align-items: center;
    margin-bottom: 12px;

    .el-form-item__label {
      font-size: 14px !important;
      color: #606266;
      text-align: left;
      padding-right: 10px;
      flex-shrink: 0;
      width: auto !important;
    }

    .el-form-item__content {
      margin-left: 0 !important;
      flex-grow: 1;

      .el-input__inner {
        text-align: left;
        padding-left: 8px;
        width: 100%;
      }
    }
  }
}

.params-container {
  padding: 16px;
  border-radius: 4px;
  min-width: 280px;
}

.empty-tip {
  padding: 20px;
  color: #909399;
  text-align: center;
}

.param-input {
  width: 100%;
}

.drawer-footer {
  position: absolute;
  bottom: 0;
  width: 100%;
  border-top: 1px solid #e8e8e8;
  padding: 10px 16px;
  text-align: center;
  background: #fff;
}

.info-icon {
  width: 16px;
  height: 16px;
  margin-right: 1vh;
}

.custom-close-btn {
  position: absolute;
  top: 50%;
  right: 10px;
  transform: translateY(-50%);
  width: 35px;
  height: 35px;
  border-radius: 50%;
  border: 2px solid #cfcfcf;
  background: none;
  font-size: 30px;
  font-weight: lighter;
  color: #cfcfcf;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1;
  padding: 0;
  outline: none;
  transition: all 0.3s;
}

.custom-close-btn:hover {
  color: #409EFF;
  border-color: #409EFF;
}

::v-deep .el-checkbox__label {
  display: none;
}


.mcp-access-point {
  border-top: 1px solid #EBEEF5;
  padding: 20px 24px;
  text-align: left;
}

.mcp-header {
  .bold-title {
    font-size: 18px;
    font-weight: bold;
    margin: 5px 0 30px 0;
  }
}

.mcp-container {
  display: flex;
  justify-content: space-between;
  gap: 30px;
}

.mcp-left,
.mcp-right {
  flex: 1;
  padding-bottom: 50px;
}

.url-header {
  margin-bottom: 8px;
  color: black;

  h4 {
    margin: 0 0 15px 0;
    font-size: 16px;
    font-weight: normal;
  }

  .address-desc {
    display: flex;
    align-items: center;
    font-size: 14px;
    margin-bottom: 12px;

    .doc-link {
      color: #1677ff;
      text-decoration: none;
      margin-left: 4px;

      &:hover {
        text-decoration: underline;
      }
    }
  }
}

.url-input {
  border-radius: 4px 0 0 4px;
  font-size: 14px;
  height: 36px;
  box-sizing: border-box;

  ::v-deep .el-input__inner {
    background-color: #f5f5f5 !important;
  }

  ::v-deep .el-input__suffix {
    right: 0;
    display: flex;
    align-items: center;
    padding-right: 10px;

    .inner-copy-btn {
      pointer-events: auto;
      border: none;
      background: #1677ff;
      color: white;
      padding: 6px;
      margin-top: 4px;
      margin-left: 4px;
    }
  }
}

.mcp-right {
  h4 {
    margin: 0 0 10px 0;
    font-size: 16px;
    font-weight: normal;
    color: black;
  }
}

.status-container {
  display: flex;
  align-items: center;

  .status-indicator {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 8px;

    &.disconnected {
      background-color: #909399;
      /* 灰色 - 未连接 */
    }

    &.connected {
      background-color: #67C23A;
      /* 绿色 - 已连接 */
    }

    &.loading {
      background-color: #E6A23C;
      /* 橙色 - 加载中 */
      animation: pulse 1.5s infinite;
    }
  }

  .status-text {
    font-size: 14px;
    margin-right: 10px;
  }

  .refresh-btn {
    display: flex;
    align-items: center;
    padding: 2px 10px;
    background: white;
    color: black;
    border: 1px solid #DCDFE6;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    transition: all 0.3s;

    &:hover {
      background: #1677ff;
      color: white;
      border-color: #1677ff;
    }

    .refresh-icon {
      margin-right: 6px;
      font-size: 14px;
    }
  }
}

@keyframes pulse {
  0% {
    opacity: 1;
  }

  50% {
    opacity: 0.4;
  }

  100% {
    opacity: 1;
  }
}

.mcp-tools-list {
  margin-top: 10px;

  .tools-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .tool-btn {
    padding: 6px 12px;
    border-color: #1677ff;
    color: #1677ff;
    background-color: white;
    font-size: 12px;

    &:hover {
      background-color: #1677ff;
      color: white;
      border-color: #1677ff;
    }
  }

  .no-tools {
    text-align: center;
    color: #909399;
    font-size: 14px;
    padding: 10px 0;
  }
}
</style>