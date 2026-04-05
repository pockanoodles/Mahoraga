(() => {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("user-input");
  const sendBtn = document.getElementById("send-btn");
  const messages = document.getElementById("messages");

  function appendMessage(role, text) {
    const wrapper = document.createElement("div");
    wrapper.classList.add("message", role);

    const bubble = document.createElement("div");
    bubble.classList.add("bubble");
    bubble.textContent = text;

    wrapper.appendChild(bubble);
    messages.appendChild(wrapper);
    messages.scrollTop = messages.scrollHeight;
    return bubble;
  }

  function setLoading(loading) {
    sendBtn.disabled = loading;
    input.disabled = loading;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const text = input.value.trim();
    if (!text) return;

    appendMessage("user", text);
    input.value = "";
    setLoading(true);

    const assistantBubble = appendMessage("assistant", "");

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, user_id: "web-user" }),
      });

      if (!res.ok) {
        assistantBubble.textContent = `Error: ${res.status} ${res.statusText}`;
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // keep incomplete line

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const chunk = line.slice(6); // strip "data: "
          if (chunk.trimEnd() === "[DONE]") break;
          assistantBubble.textContent += chunk;
          messages.scrollTop = messages.scrollHeight;
        }
      }
    } catch (err) {
      assistantBubble.textContent = `Network error: ${err.message}`;
    } finally {
      setLoading(false);
      input.focus();
    }
  });
})();
