const
  CoreGptApiKey = 0;
  CoreGptBaseUrl = 1;
  CoreGptModelEngine = 2;
  CoreEmbeddingModel = 3;
  CoreFayUrl = 4;
  CoreProxyConfig = 5;

  AliAppKey = 0;
  AliKeyId = 1;
  AliKeySecret = 2;

var
  PublicConfigCenterId: String;
  ConfigSourcePage: TInputOptionWizardPage;
  PublicConfigPage: TInputQueryWizardPage;
  CoreConfigPage: TInputQueryWizardPage;
  StartModePage: TInputOptionWizardPage;
  AliKeyConfigPage: TInputQueryWizardPage;
  ReviewPage: TWizardPage;
  ReviewMemo: TNewMemo;

function UsePublicConfig(): Boolean;
begin
  Result := ConfigSourcePage.Values[0];
end;

function UseUpgrade(): Boolean;
begin
  Result := ConfigSourcePage.Values[2];
end;

function HasExistingRootConfig(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\system.conf')) or
            FileExists(ExpandConstant('{app}\config.json'));
end;

function GetPublicConfigCenterId(): String;
begin
  if PublicConfigPage <> nil then
    Result := Trim(PublicConfigPage.Values[0])
  else
    Result := '';

  if Result = '' then
    Result := PublicConfigCenterId;
end;

function GetLaunchParameters(Param: String): String;
begin
  if UseUpgrade() then
    Result := 'start'
  else if UsePublicConfig() and (not HasExistingRootConfig()) then
    Result := 'start -center_config ' + GetPublicConfigCenterId()
  else
    Result := 'start';
end;

function GetCoreValue(Index: Integer): String;
begin
  Result := Trim(CoreConfigPage.Values[Index]);
end;

function GetAliValue(Index: Integer): String;
begin
  Result := Trim(AliKeyConfigPage.Values[Index]);
end;

function GetStartMode(): String;
begin
  if StartModePage.Values[0] then
    Result := 'web'
  else
    Result := 'common';
end;

function GetDefaultConfigJson(): String;
begin
  Result :=
    '{'#13#10 +
    '  "attribute": {'#13#10 +
    '    "additional": "",'#13#10 +
    '    "age": "",'#13#10 +
    '    "birth": "",'#13#10 +
    '    "constellation": "",'#13#10 +
    '    "contact": "",'#13#10 +
    '    "gender": "",'#13#10 +
    '    "goal": "assistant",'#13#10 +
    '    "hobby": "",'#13#10 +
    '    "job": "assistant",'#13#10 +
    '    "name": "Feifei",'#13#10 +
    '    "position": "companion",'#13#10 +
    '    "voice": "abin",'#13#10 +
    '    "zodiac": ""'#13#10 +
    '  },'#13#10 +
    '  "interact": {'#13#10 +
    '    "QnA": "qa.csv",'#13#10 +
    '    "maxInteractTime": 15,'#13#10 +
    '    "perception": {'#13#10 +
    '      "chat": 10,'#13#10 +
    '      "follow": 10,'#13#10 +
    '      "gift": 10,'#13#10 +
    '      "indifferent": 10,'#13#10 +
    '      "join": 10'#13#10 +
    '    },'#13#10 +
    '    "playSound": false,'#13#10 +
    '    "visualization": false'#13#10 +
    '  },'#13#10 +
    '  "items": [],'#13#10 +
    '  "memory": {'#13#10 +
    '    "isolate_by_user": true,'#13#10 +
    '    "use_bionic_memory": false'#13#10 +
    '  },'#13#10 +
    '  "source": {'#13#10 +
    '    "automatic_player_status": false,'#13#10 +
    '    "automatic_player_url": "http://127.0.0.1:6000",'#13#10 +
    '    "liveRoom": {'#13#10 +
    '      "enabled": true,'#13#10 +
    '      "url": ""'#13#10 +
    '    },'#13#10 +
    '    "record": {'#13#10 +
    '      "device": "",'#13#10 +
    '      "enabled": false'#13#10 +
    '    },'#13#10 +
    '    "wake_word": "feifei",'#13#10 +
    '    "wake_word_enabled": false,'#13#10 +
    '    "wake_word_type": "front"'#13#10 +
    '  }'#13#10 +
    '}'#13#10;
end;

function BuildSystemConfContent(): String;
begin
  Result :=
    '[key]'#13#10 +
    'ali_nls_app_key = ' + GetAliValue(AliAppKey) + #13#10 +
    'ali_nls_key_id = ' + GetAliValue(AliKeyId) + #13#10 +
    'ali_nls_key_secret = ' + GetAliValue(AliKeySecret) + #13#10 +
    'ali_tss_app_key = ' + GetAliValue(AliAppKey) + #13#10 +
    'ali_tss_key_id = ' + GetAliValue(AliKeyId) + #13#10 +
    'ali_tss_key_secret = ' + GetAliValue(AliKeySecret) + #13#10 +
    'asr_mode = ali'#13#10 +
    'baidu_emotion_app_id = '#13#10 +
    'baidu_emotion_api_key = '#13#10 +
    'embedding_api_model = ' + GetCoreValue(CoreEmbeddingModel) + #13#10 +
    'fay_url = ' + GetCoreValue(CoreFayUrl) + #13#10 +
    'gpt_api_key = ' + GetCoreValue(CoreGptApiKey) + #13#10 +
    'gpt_base_url = ' + GetCoreValue(CoreGptBaseUrl) + #13#10 +
    'gpt_model_engine = ' + GetCoreValue(CoreGptModelEngine) + #13#10 +
    'local_asr_ip = 127.0.0.1'#13#10 +
    'local_asr_port = 10197'#13#10 +
    'ms_tts_key = '#13#10 +
    'ms_tts_region = '#13#10 +
    'proxy_config = ' + GetCoreValue(CoreProxyConfig) + #13#10 +
    'start_mode = ' + GetStartMode() + #13#10 +
    'tts_module = ali'#13#10 +
    'volcano_tts_access_token = '#13#10 +
    'volcano_tts_appid = '#13#10 +
    'volcano_tts_cluster = volcano_tts'#13#10 +
    'volcano_tts_voice_type = '#13#10;
end;

function ValidateRequired(const Value, FieldName: String): Boolean;
begin
  Result := Trim(Value) <> '';
  if not Result then
    MsgBox(FieldName + ' 不能为空。', mbError, MB_OK);
end;

procedure UpdateReviewMemo();
begin
  ReviewMemo.Text :=
    '安装程序将在以下目录生成本地配置文件：'#13#10 +
    WizardDirValue + #13#10#13#10 +
    'system.conf：'#13#10#13#10 +
    BuildSystemConfContent() + #13#10 +
    'config.json：'#13#10#13#10 +
    GetDefaultConfigJson();
end;

procedure WriteManualConfig();
begin
  SaveStringToFile(ExpandConstant('{app}\system.conf'), BuildSystemConfContent(), False);
  SaveStringToFile(ExpandConstant('{app}\config.json'), GetDefaultConfigJson(), False);
end;

procedure RemoveBundledConfigCache();
begin
  DeleteFile(ExpandConstant('{app}\cache_data\system.conf'));
  DeleteFile(ExpandConstant('{app}\cache_data\config.json'));
end;

procedure InitializeWizard();
begin
  PublicConfigCenterId := '2d431d0a-3083-4cbd-8e7f-23f732e237fb';
  ConfigSourcePage := CreateInputOptionPage(
    wpSelectDir,
    '初始配置',
    '请选择 Fay 首次启动时使用的配置来源',
    '如果选择"使用公共配置（不稳定）"，安装后首次启动会从配置中心下载配置。'#13#10 +
    '如果选择"手动填写本地配置"，安装程序会根据你填写的内容生成 system.conf，并自动补一个默认 config.json。'#13#10 +
    '如果选择"升级"，将保留现有配置文件，仅更新程序文件。',
    True,
    False
  );
  ConfigSourcePage.Add('使用公共配置（不稳定）');
  ConfigSourcePage.Add('手动填写本地配置');
  ConfigSourcePage.Add('升级（保留现有配置，仅更新程序）');
  ConfigSourcePage.SelectedValueIndex := 0;

  PublicConfigPage := CreateInputQueryPage(
    ConfigSourcePage.ID,
    '公共配置',
    '填写首次启动时使用的配置中心项目 ID',
    '当你选择"使用公共配置（不稳定）"时，安装后的启动参数会使用这里填写的 center_config。'#13#10 +
    '如果你不修改，将默认使用下面这个项目 ID。'
  );
  PublicConfigPage.Add('center_config（默认值可直接使用）', False);
  PublicConfigPage.Values[0] := PublicConfigCenterId;

  CoreConfigPage := CreateInputQueryPage(
    PublicConfigPage.ID,
    '大模型与网络配置',
    '填写 system.conf 的核心字段',
    '以下内容会直接写入 system.conf。'#13#10 +
    'gpt_base_url 示例为 https://api.openai.com/v1；proxy_config 可留空，也可填写 127.0.0.1:7890。'
  );
  CoreConfigPage.Add('gpt_api_key（必填，LLM 平台 API Key）', False);
  CoreConfigPage.Add('gpt_base_url（必填，例如 https://api.openai.com/v1）', False);
  CoreConfigPage.Add('gpt_model_engine（必填，例如 glm4 / deepseek / qwen3-4b）', False);
  CoreConfigPage.Add('embedding_api_model（必填，Embedding 模型名）', False);
  CoreConfigPage.Add('fay_url（必填，默认 http://127.0.0.1:5000）', False);
  CoreConfigPage.Add('proxy_config（选填，例如 127.0.0.1:7890）', False);
  CoreConfigPage.Values[CoreGptApiKey] := '';
  CoreConfigPage.Values[CoreGptBaseUrl] := '';
  CoreConfigPage.Values[CoreGptModelEngine] := '';
  CoreConfigPage.Values[CoreEmbeddingModel] := '';
  CoreConfigPage.Values[CoreFayUrl] := 'http://127.0.0.1:5000';
  CoreConfigPage.Values[CoreProxyConfig] := '';

  StartModePage := CreateInputOptionPage(
    CoreConfigPage.ID,
    '启动模式',
    '请选择 Fay 的启动模式',
    '选择 web 模式将启动 Web 界面；选择 common 模式为客户端窗口模式。',
    True,
    False
  );
  StartModePage.Add('web（Web 界面模式，推荐）');
  StartModePage.Add('common（客户端窗口模式）');
  StartModePage.SelectedValueIndex := 0;

  { --- 阿里云语音密钥配置页（3 个字段，ASR 和 TTS 共用） --- }
  AliKeyConfigPage := CreateInputQueryPage(
    StartModePage.ID,
    '阿里云语音服务配置',
    '填写阿里云语音识别和语音合成所需密钥',
    '语音识别（ASR）和语音合成（TTS）共用同一组阿里云密钥。'#13#10 +
    '这些密钥可在阿里云智能语音交互控制台获取。'
  );
  AliKeyConfigPage.Add('ali_app_key（阿里云语音服务 AppKey）', False);
  AliKeyConfigPage.Add('ali_key_id（阿里云语音服务 KeyId）', False);
  AliKeyConfigPage.Add('ali_key_secret（阿里云语音服务 KeySecret）', False);

  ReviewPage := CreateCustomPage(
    AliKeyConfigPage.ID,
    '预览本地配置',
    '请确认即将生成的配置文件内容'
  );
  ReviewMemo := TNewMemo.Create(ReviewPage);
  ReviewMemo.Parent := ReviewPage.Surface;
  ReviewMemo.Left := 0;
  ReviewMemo.Top := 0;
  ReviewMemo.Width := ReviewPage.SurfaceWidth;
  ReviewMemo.Height := ReviewPage.SurfaceHeight + ReviewPage.SurfaceExtraHeight;
  ReviewMemo.ReadOnly := True;
  ReviewMemo.ScrollBars := ssBoth;
  ReviewMemo.WordWrap := False;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;

  { 升级模式：跳过所有配置页 }
  if UseUpgrade() and (
    (PageID = PublicConfigPage.ID) or
    (PageID = CoreConfigPage.ID) or
    (PageID = StartModePage.ID) or
    (PageID = AliKeyConfigPage.ID) or
    (PageID = ReviewPage.ID)
  ) then
  begin
    Result := True;
    Exit;
  end;

  if (not UsePublicConfig()) and (PageID = PublicConfigPage.ID) then
  begin
    Result := True;
    Exit;
  end;

  if UsePublicConfig() and (
    (PageID = CoreConfigPage.ID) or
    (PageID = StartModePage.ID) or
    (PageID = AliKeyConfigPage.ID) or
    (PageID = ReviewPage.ID)
  ) then
    Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if UsePublicConfig() and (CurPageID = PublicConfigPage.ID) then
  begin
    Result := ValidateRequired(GetPublicConfigCenterId(), 'center_config');
    Exit;
  end;

  if UsePublicConfig() then
    Exit;

  if CurPageID = CoreConfigPage.ID then
  begin
    Result := ValidateRequired(GetCoreValue(CoreGptApiKey), 'gpt_api_key');
    if not Result then Exit;
    Result := ValidateRequired(GetCoreValue(CoreGptBaseUrl), 'gpt_base_url');
    if not Result then Exit;
    Result := ValidateRequired(GetCoreValue(CoreGptModelEngine), 'gpt_model_engine');
    if not Result then Exit;
    Result := ValidateRequired(GetCoreValue(CoreEmbeddingModel), 'embedding_api_model');
    if not Result then Exit;
    Result := ValidateRequired(GetCoreValue(CoreFayUrl), 'fay_url');
    if not Result then Exit;
  end;

  if CurPageID = AliKeyConfigPage.ID then
  begin
    Result := ValidateRequired(GetAliValue(AliAppKey), 'ali_app_key');
    if not Result then Exit;
    Result := ValidateRequired(GetAliValue(AliKeyId), 'ali_key_id');
    if not Result then Exit;
    Result := ValidateRequired(GetAliValue(AliKeySecret), 'ali_key_secret');
    if not Result then Exit;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { 动态控制"升级"选项的可用性：仅当目标目录存在旧配置时启用 }
  if CurPageID = ConfigSourcePage.ID then
  begin
    if HasExistingRootConfig() then
    begin
      ConfigSourcePage.CheckListBox.ItemEnabled[2] := True;
      ConfigSourcePage.CheckListBox.ItemCaption[2] := '升级（保留现有配置，仅更新程序）';
    end
    else
    begin
      ConfigSourcePage.CheckListBox.ItemEnabled[2] := False;
      ConfigSourcePage.CheckListBox.ItemCaption[2] := '升级（未检测到旧版安装）';
      { 如果当前选中的是升级但已不可用，回退到第一项 }
      if ConfigSourcePage.SelectedValueIndex = 2 then
        ConfigSourcePage.SelectedValueIndex := 0;
    end;
  end;

  if (CurPageID = ReviewPage.ID) and (not UsePublicConfig()) then
    UpdateReviewMemo();
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if UseUpgrade() then
      begin end { 升级模式：不写任何配置文件，保留用户现有配置 }
    else if UsePublicConfig() then
      RemoveBundledConfigCache()
    else
      WriteManualConfig();
  end;
end;
