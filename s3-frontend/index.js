const historySidebar = document.getElementById("historySidebar");
const collapseBtn = document.getElementById("collapseBtn");

collapseBtn.addEventListener("click", () => {
  const isCollapsed = historySidebar.classList.toggle("collapsed");
  collapseBtn.innerHTML = isCollapsed ? "&lt;" : "&gt;";
  collapseBtn.setAttribute("aria-label", isCollapsed ? "Expand sidebar" : "Collapse sidebar");
});

// placeholders will be automatically replaced with API Gateway URL by GitHub Actions
const API_BASE_URL = "__API_URL__/project-stage";
const GENERATE_URL = `${API_BASE_URL}/generate`;
const SAVE_URL = `${API_BASE_URL}/save`;
const SAVED_URL = `${API_BASE_URL}/saved`;
const DELETE_URL = `${API_BASE_URL}/delete`;
const DOWNLOAD_URL = `${API_BASE_URL}/download`;

const inputBox = document.getElementById("inputBox");
const outputBox = document.getElementById("outputBox");
const submitBtn = document.getElementById("submitBtn");
const saveBtn = document.getElementById("saveBtn");
const historyList = document.getElementById("historyList");
const logoutBtn = document.getElementById("logoutBtn");
const downloadBtn = document.getElementById("downloadBtn");

let latestOutput = null;

function getIdToken() {
  return localStorage.getItem("app_id_token");
}

function requireLogin() {
  const idToken = getIdToken();
  if (!idToken) {
    outputBox.value = "You are not logged in.";
    window.location.href = "login";
    return null;
  }
  return idToken;
}

// check login.js
logoutBtn.addEventListener("click", () => {
  localStorage.removeItem("app_id_token");
  window.location.href = "login";
});

submitBtn.addEventListener("click", async () => {
  const prompt = inputBox.value.trim();
  const idToken = requireLogin();

  if (!idToken) return;

  if (!prompt) {
    outputBox.value = "Please enter some text.";
    return;
  }

  submitBtn.disabled = true;
  saveBtn.disabled = true;
  latestOutput = null;
  outputBox.value = "Processing...";

  try {
    const res = await fetch(GENERATE_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${idToken}`
      },
      body: JSON.stringify({ prompt })
    });

    const data = await res.json();

    if (!res.ok) {
      outputBox.value = JSON.stringify(data, null, 2);
      return;
    }

    latestOutput = data.output;
    outputBox.value = JSON.stringify(latestOutput, null, 2);
    saveBtn.disabled = false;

  } catch (error) {
    outputBox.value = "Error! " + error.message;
  } finally {
    submitBtn.disabled = false;
  }
});

saveBtn.addEventListener("click", async () => {
  const idToken = requireLogin();

  if (!idToken) return;

  if (!latestOutput) {
    outputBox.value = "Please structure data before saving.";
    return;
  }

  saveBtn.disabled = true;
  saveBtn.textContent = "Saving...";

  try {
    const res = await fetch(SAVE_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${idToken}`
      },
      body: JSON.stringify({ output: latestOutput })
    });

    const data = await res.json();

    if (!res.ok) {
      outputBox.value = JSON.stringify(data, null, 2);
      saveBtn.disabled = false;
      return;
    }

    outputBox.value = JSON.stringify(data.item, null, 2);
    latestOutput = data.item;
    await loadSavedItems();

  } catch (error) {
    outputBox.value = "Error! " + error.message;
    saveBtn.disabled = false;
  } finally {
    saveBtn.textContent = "Save";
  }
});

downloadBtn.addEventListener("click", async () => {
  const idToken = requireLogin();

  if (!idToken) return;

  downloadBtn.disabled = true;
  downloadBtn.textContent = "Downloading...";

  try {
    const res = await fetch(DOWNLOAD_URL, {
      method: "GET",
      headers: {
        "Authorization": `Bearer ${idToken}`
      }
    });

    if (!res.ok) {
      const data = await res.json();
      outputBox.value = JSON.stringify(data, null, 2);
      return;
    }

    const blob = await res.blob();
    const objectUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = objectUrl;
    link.download = "saved-data.xlsx";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(objectUrl);

  } catch (error) {
    outputBox.value = "Error! " + error.message;
  } finally {
    downloadBtn.disabled = false;
    downloadBtn.textContent = "Download Excel";
  }
});

async function loadSavedItems() {
  const idToken = getIdToken();

  if (!idToken) {
    window.location.href = "login";
    return;
  }

  try {
    const res = await fetch(SAVED_URL, {
      method: "GET",
      headers: {
        "Authorization": `Bearer ${idToken}`
      }
    });

    const data = await res.json();

    if (!res.ok) {
      historyList.innerHTML = `<li class="history-item">Could not load saved items.</li>`;
      return;
    }

    renderSavedItems(data.items || []);

  } catch (error) {
    historyList.innerHTML = `<li class="history-item">Error loading saved items.</li>`;
  }
}

async function deleteSavedItem(item) {
  const idToken = requireLogin();

  if (!idToken) return;

  if (!item.id) {
    outputBox.value = "This saved item does not have an id, so it cannot be deleted.";
    return;
  }

  try {
    const res = await fetch(DELETE_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${idToken}`
      },
      body: JSON.stringify({ id: item.id })
    });

    const data = await res.json();

    if (!res.ok) {
      outputBox.value = JSON.stringify(data, null, 2);
      return;
    }

    if (latestOutput && latestOutput.id === item.id) {
      latestOutput = null;
      outputBox.value = "Deleted saved item.";
      saveBtn.disabled = true;
    }

    await loadSavedItems();

  } catch (error) {
    outputBox.value = "Error! " + error.message;
  }
}

function renderSavedItems(items) {
  historyList.innerHTML = "";

  if (!items.length) {
    historyList.innerHTML = `<li class="history-item">No saved items yet.</li>`;
    return;
  }

  items.forEach((item) => {
    const li = document.createElement("li");
    li.className = "history-item";

    const displayItem = { ...item };
    delete displayItem.userId;

    const label =
      displayItem.name ||
      displayItem.title ||
      displayItem.major ||
      displayItem.id ||
      "Saved item";

    const labelSpan = document.createElement("span");
    labelSpan.className = "history-label";
    labelSpan.textContent = label;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "delete-saved-btn";
    removeBtn.textContent = "×";
    removeBtn.setAttribute("aria-label", `Remove ${label}`);

    li.title = JSON.stringify(displayItem, null, 2);

    li.addEventListener("click", () => {
      document.querySelectorAll(".history-item").forEach((el) => {
        el.classList.remove("active");
      });

      li.classList.add("active");

      latestOutput = displayItem;
      outputBox.value = JSON.stringify(displayItem, null, 2);
      saveBtn.disabled = false;
    });

    removeBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      removeBtn.disabled = true;
      await deleteSavedItem(displayItem);
    });

    li.appendChild(labelSpan);
    li.appendChild(removeBtn);
    historyList.appendChild(li);
  });
}

loadSavedItems();
