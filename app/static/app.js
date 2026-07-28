const STATUS_LABELS = {
  todo: "할 일",
  doing: "진행 중",
  done: "완료",
};

const board = document.querySelector("#board");
const dialog = document.querySelector("#task-dialog");
const form = document.querySelector("#task-form");
const titleInput = document.querySelector("#task-title");
const descriptionInput = document.querySelector("#task-description");
const statusInput = document.querySelector("#task-status");
const taskIdInput = document.querySelector("#task-id");
const notification = document.querySelector("#notification");
const emptyStateTemplate = document.querySelector("#empty-state-template");

let tasks = [];
let draggedTaskId = null;
let notificationTimer = null;

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  if (!response.ok) {
    let message = "요청을 처리하지 못했습니다.";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      // Keep the friendly fallback when the server did not return JSON.
    }
    throw new Error(message);
  }

  return response.status === 204 ? null : response.json();
}

function showError(message) {
  window.clearTimeout(notificationTimer);
  notification.textContent = message;
  notification.hidden = false;
  notificationTimer = window.setTimeout(() => {
    notification.hidden = true;
  }, 5000);
}

function taskCard(task) {
  const card = document.createElement("article");
  card.className = "task-card";
  card.draggable = true;
  card.dataset.taskId = task.id;
  card.innerHTML = `
    <div class="task-title-row">
      <h3 class="task-title"></h3>
      <div class="task-actions">
        <button class="icon-button edit-task" type="button" aria-label="작업 수정">✎</button>
        <button class="icon-button delete-task" type="button" aria-label="작업 삭제">×</button>
      </div>
    </div>
  `;
  card.querySelector(".task-title").textContent = task.title;

  if (task.description) {
    const description = document.createElement("p");
    description.className = "task-description";
    description.textContent = task.description;
    card.append(description);
  }

  card.querySelector(".edit-task").addEventListener("click", () => openTaskDialog(task));
  card.querySelector(".delete-task").addEventListener("click", () => deleteTask(task));
  card.addEventListener("dragstart", handleDragStart);
  card.addEventListener("dragend", handleDragEnd);
  return card;
}

function render() {
  Object.keys(STATUS_LABELS).forEach((status) => {
    const list = document.querySelector(`[data-task-list="${status}"]`);
    const statusTasks = tasks
      .filter((task) => task.status === status)
      .sort((left, right) => left.position - right.position);

    list.replaceChildren();
    if (statusTasks.length === 0) {
      list.append(emptyStateTemplate.content.cloneNode(true));
    } else {
      statusTasks.forEach((task) => list.append(taskCard(task)));
    }
    document.querySelector(`[data-count="${status}"]`).textContent = statusTasks.length;
  });
}

async function loadTasks() {
  tasks = await request("/api/tasks");
  render();
}

function openTaskDialog(task = null, initialStatus = "todo") {
  form.reset();
  taskIdInput.value = task?.id ?? "";
  titleInput.value = task?.title ?? "";
  descriptionInput.value = task?.description ?? "";
  statusInput.value = task?.status ?? initialStatus;
  document.querySelector("#dialog-title").textContent = task ? "작업 수정" : "새 작업";
  dialog.showModal();
  titleInput.focus();
}

function closeTaskDialog() {
  dialog.close();
}

async function saveTask(event) {
  event.preventDefault();
  const id = taskIdInput.value;
  const payload = {
    title: titleInput.value,
    description: descriptionInput.value,
    status: statusInput.value,
  };

  try {
    if (id) {
      await request(`/api/tasks/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
    } else {
      await request("/api/tasks", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    }
    closeTaskDialog();
    await loadTasks();
  } catch (error) {
    showError(error.message);
  }
}

async function deleteTask(task) {
  if (!window.confirm(`“${task.title}” 작업을 삭제할까요?`)) {
    return;
  }

  try {
    await request(`/api/tasks/${task.id}`, { method: "DELETE" });
    await loadTasks();
  } catch (error) {
    showError(error.message);
  }
}

function handleDragStart(event) {
  draggedTaskId = Number(event.currentTarget.dataset.taskId);
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", String(draggedTaskId));
  requestAnimationFrame(() => event.currentTarget.classList.add("is-dragging"));
}

function handleDragEnd() {
  draggedTaskId = null;
  document.querySelectorAll(".is-dragging, .is-drop-target").forEach((element) => {
    element.classList.remove("is-dragging", "is-drop-target");
  });
}

function insertionIndex(list, pointerY) {
  const cards = [...list.querySelectorAll(".task-card:not(.is-dragging)")];
  const nextCard = cards.find((card) => {
    const bounds = card.getBoundingClientRect();
    return pointerY < bounds.top + bounds.height / 2;
  });
  return nextCard ? cards.indexOf(nextCard) : cards.length;
}

async function handleDrop(event) {
  event.preventDefault();
  const column = event.currentTarget.closest(".column");
  const targetStatus = column.dataset.status;
  const taskId = draggedTaskId || Number(event.dataTransfer.getData("text/plain"));
  const task = tasks.find((item) => item.id === taskId);

  if (!task) {
    return;
  }

  const previousTasks = structuredClone(tasks);
  const list = column.querySelector(".task-list");
  const position = insertionIndex(list, event.clientY);

  const oldStatus = task.status;
  tasks = tasks.filter((item) => item.id !== taskId);
  tasks
    .filter((item) => item.status === oldStatus)
    .sort((left, right) => left.position - right.position)
    .forEach((item, index) => {
      item.position = index;
    });

  const targetTasks = tasks
    .filter((item) => item.status === targetStatus)
    .sort((left, right) => left.position - right.position);
  task.status = targetStatus;
  targetTasks.splice(Math.min(position, targetTasks.length), 0, task);
  targetTasks.forEach((item, index) => {
    item.position = index;
  });
  tasks.push(task);
  render();

  try {
    await request(`/api/tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: targetStatus, position }),
    });
    await loadTasks();
  } catch (error) {
    tasks = previousTasks;
    render();
    showError(`이동을 저장하지 못했습니다. ${error.message}`);
  }
}

document.querySelector("#new-task-button").addEventListener("click", () => openTaskDialog());
document.querySelectorAll("[data-add-status]").forEach((button) => {
  button.addEventListener("click", () => openTaskDialog(null, button.dataset.addStatus));
});
document.querySelector("#close-dialog").addEventListener("click", closeTaskDialog);
document.querySelector("#cancel-dialog").addEventListener("click", closeTaskDialog);
form.addEventListener("submit", saveTask);
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) {
    closeTaskDialog();
  }
});

board.querySelectorAll(".column").forEach((column) => {
  column.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    column.classList.add("is-drop-target");
  });
  column.addEventListener("dragleave", (event) => {
    if (!column.contains(event.relatedTarget)) {
      column.classList.remove("is-drop-target");
    }
  });
  column.addEventListener("drop", handleDrop);
});

loadTasks().catch((error) => showError(error.message));

