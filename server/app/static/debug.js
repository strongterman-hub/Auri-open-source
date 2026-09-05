const output = document.getElementById("output");
const sessionId = document.getElementById("session-id");

function log(message) {
  const time = new Date().toLocaleTimeString();
  output.textContent += `[${time}] ${message}\n`;
  output.scrollTop = output.scrollHeight;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(JSON.stringify(data || { status: response.status }));
  }
  return data;
}

document.getElementById("health-btn").addEventListener("click", async () => {
  try {
    const data = await api("/v1/health");
    log(`health: ${JSON.stringify(data)}`);
  } catch (error) {
    log(`health error: ${error.message}`);
  }
});

document.getElementById("session-btn").addEventListener("click", async () => {
  try {
    const data = await api("/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ user_id: "debug-user" }),
    });
    sessionId.textContent = data.id;
    log(`session created: ${data.id}`);
  } catch (error) {
    log(`create session error: ${error.message}`);
  }
});

document.getElementById("memory-write-btn").addEventListener("click", async () => {
  if (!sessionId.textContent || sessionId.textContent === "no session") {
    log("请先创建 session");
    return;
  }
  const action = document.getElementById("memory-action").value;
  const target = document.getElementById("memory-target").value;
  const content = document.getElementById("memory-content").value;
  const oldText = document.getElementById("memory-old-text").value;

  try {
    const data = await api(`/v1/sessions/${sessionId.textContent}/memory`, {
      method: "POST",
      body: JSON.stringify({
        operations: [{ action, target, content: content || null, old_text: oldText || null }],
      }),
    });
    log(`memory write: ${JSON.stringify(data)}`);
  } catch (error) {
    log(`memory write error: ${error.message}`);
  }
});

document.getElementById("memory-read-btn").addEventListener("click", async () => {
  if (!sessionId.textContent || sessionId.textContent === "no session") {
    log("请先创建 session");
    return;
  }
  try {
    const data = await api(`/v1/sessions/${sessionId.textContent}/memory`);
    log(`memory snapshot: ${JSON.stringify(data)}`);
  } catch (error) {
    log(`memory read error: ${error.message}`);
  }
});

document.getElementById("send-btn").addEventListener("click", async () => {
  if (!sessionId.textContent || sessionId.textContent === "no session") {
    log("请先创建 session");
    return;
  }
  const content = document.getElementById("message-input").value;
  if (!content.trim()) {
    log("消息不能为空");
    return;
  }

  try {
    const data = await api(`/v1/sessions/${sessionId.textContent}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    log(`agent: ${JSON.stringify(data)}`);
  } catch (error) {
    log(`agent error: ${error.message}`);
  }
});

log("Debug console ready.");
