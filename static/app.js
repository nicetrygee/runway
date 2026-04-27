// AJAX status toggle — no page reload needed
document.querySelectorAll(".status-select").forEach(select => {
  select.addEventListener("change", async function () {
    const id = this.dataset.id;
    const status = this.value;
    const card = this.closest(".task-card");

    const res = await fetch(`/status/${id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status })
    });

    if (res.ok) {
      // Move card to the correct column
      const targetCol = document.querySelector(`.kanban-col .status-header-${status}`)
        ?.closest(".kanban-col")
        ?.querySelector(".kanban-cards");

      if (targetCol) {
        card.className = card.className.replace(/status-\S+/, `status-${status}`);
        targetCol.appendChild(card);

        // Update the option to reflect new status
        card.querySelectorAll(".status-select option").forEach(opt => {
          opt.selected = opt.value === status;
        });
      }
    } else {
      alert("Failed to update status.");
    }
  });
});
