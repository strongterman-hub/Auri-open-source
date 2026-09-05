function resolveDownloadUrl(value) {
  if (!value) return null;
  try {
    const absolute = new URL(value, window.location.origin);
    if (value.startsWith("http://") || value.startsWith("https://")) {
      return ["http:", "https:"].includes(absolute.protocol) ? absolute.href : null;
    }
    if (value.startsWith("/v1/")) return value;
    if (value.startsWith("/")) return `/v1${value}`;
    return `/v1/${value.replace(/^\/+/, "")}`;
  } catch {
    return null;
  }
}

function initDownload() {
  const button = document.querySelector("#download-button");
  if (!button) return;

  const label = document.querySelector("#download-label");
  const error = document.querySelector("#download-error");
  const retry = document.querySelector("#retry-release");
  const notice = document.querySelector("#platform-notice");

  const setLoading = () => {
    button.removeAttribute("href");
    button.setAttribute("aria-disabled", "true");
    label.textContent = "正在准备下载";
    error.classList.remove("is-visible");
  };

  const setError = () => {
    button.removeAttribute("href");
    button.setAttribute("aria-disabled", "true");
    label.textContent = "暂时无法下载";
    error.classList.add("is-visible");
  };

  const loadRelease = async () => {
    setLoading();
    try {
      const response = await fetch("/v1/update/check", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const release = await response.json();
      const url = resolveDownloadUrl(release.download_url);
      if (!url) throw new Error("missing download URL");
      button.href = url;
      button.setAttribute("aria-disabled", "false");
      label.textContent = "下载 Android 版";
    } catch {
      setError();
    }
  };

  if (/iPhone|iPad|iPod/i.test(navigator.userAgent)) {
    notice.textContent = "当前仅支持 Android";
  }
  retry?.addEventListener("click", loadRelease);
  loadRelease();
}

function initScreenSwitcher() {
  const buttons = [...document.querySelectorAll("[data-screen-target]")];
  const screens = [...document.querySelectorAll("[data-screen]")];
  if (!buttons.length || !screens.length) return;

  const selectScreen = (button) => {
    const target = button.dataset.screenTarget;
    buttons.forEach((item) => {
      const selected = item === button;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    screens.forEach((screen) => {
      const selected = screen.dataset.screen === target;
      screen.classList.toggle("is-active", selected);
      screen.setAttribute("aria-hidden", String(!selected));
      screen.hidden = !selected;
    });
  };

  buttons.forEach((button, index) => {
    button.addEventListener("click", () => selectScreen(button));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const delta = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1;
      const next = event.key === "Home" ? buttons[0] : event.key === "End" ? buttons.at(-1) : buttons[(index + delta + buttons.length) % buttons.length];
      selectScreen(next);
      next.focus();
    });
  });
}

initDownload();
initScreenSwitcher();
