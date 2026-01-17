const audio = document.getElementById("audio");
const playBtn = document.getElementById("playBtn");
const stopBtn = document.getElementById("stopBtn");
const statusText = document.getElementById("statusText");
const statusDot = document.getElementById("statusDot");
const installBtn = document.getElementById("installBtn");

const streamUrl = "/hls/live.m3u8";
let hls;
let installPrompt;

function setStatus(isLive) {
  // Toggle the 'live' class on the container (id="statusDot")
  statusDot.classList.toggle("live", isLive);
  statusText.textContent = isLive ? "Live now" : "Offline";
}

async function checkStream() {
  try {
    const res = await fetch(streamUrl, { method: 'HEAD', cache: "no-store" });
    setStatus(res.ok);
  } catch {
    setStatus(false);
  }
}

function setupPlayer() {
  if (window.Hls && window.Hls.isSupported()) {
    hls = new window.Hls({
      lowLatencyMode: true,
    });
    hls.loadSource(streamUrl);
    hls.attachMedia(audio);
  } else {
    audio.src = streamUrl;
  }
}

playBtn.addEventListener("click", async () => {
  if (!audio.src && !hls) {
    setupPlayer();
  }
  try {
    await audio.play();
  } catch (err) {
    console.error("Unable to start audio", err);
  }
});

stopBtn.addEventListener("click", () => {
  audio.pause();
  audio.currentTime = 0;
  if (hls) {
      hls.destroy();
      hls = null;
  }
  audio.removeAttribute('src'); 
});

// --- FIX START: Only run PWA logic if the button exists ---
if (installBtn) {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    installBtn.disabled = false;
  });

  installBtn.addEventListener("click", async () => {
    if (!installPrompt) {
      return;
    }
    installPrompt.prompt();
    await installPrompt.userChoice;
    installPrompt = null;
    installBtn.disabled = true;
  });
}
// --- FIX END ---

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js");
  });
}

// Start checking status immediately
checkStream();
setInterval(checkStream, 10000);