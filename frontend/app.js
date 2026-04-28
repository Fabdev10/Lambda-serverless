(function () {
  var form = document.getElementById("contact-form");
  var submitButton = document.getElementById("submit-button");
  var statusMessage = document.getElementById("status-message");
  var adminForm = document.getElementById("admin-form");
  var adminLoadButton = document.getElementById("admin-load-button");
  var adminLoadMoreButton = document.getElementById("admin-load-more-button");
  var adminStatusMessage = document.getElementById("admin-status-message");
  var submissionsTbody = document.getElementById("submissions-tbody");
  var adminSearchInput = document.getElementById("admin-search");
  var adminExportButton = document.getElementById("admin-export-button");
  var config = window.APP_CONFIG || {};
  var adminState = {
    token: "",
    limit: "20",
    rangeDays: "all",
    searchTerm: "",
    nextCursor: "",
    items: []
  };

  function setStatus(message, type) {
    statusMessage.textContent = message;
    statusMessage.className = "status-message";
    if (type) {
      statusMessage.classList.add("is-" + type);
    }
  }

  function setAdminStatus(message, type) {
    if (!adminStatusMessage) {
      return;
    }

    adminStatusMessage.textContent = message;
    adminStatusMessage.className = "status-message";
    if (type) {
      adminStatusMessage.classList.add("is-" + type);
    }
  }

  function isConfiguredApiUrl(url) {
    return !!url && url.indexOf("__CONTACT_API_URL__") === -1;
  }

  function getSubmissionsApiUrl() {
    if (!isConfiguredApiUrl(config.contactApiUrl)) {
      return "";
    }

    try {
      var url = new URL(config.contactApiUrl);
      url.pathname = url.pathname.replace(/\/contact\/?$/, "/submissions");
      url.search = "";
      return url.toString();
    } catch (_error) {
      return "";
    }
  }

  function clearSubmissionsTable(message) {
    if (!submissionsTbody) {
      return;
    }

    submissionsTbody.innerHTML = "";
    var row = document.createElement("tr");
    var cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "empty-row";
    cell.textContent = message;
    row.appendChild(cell);
    submissionsTbody.appendChild(row);
  }

  function formatDate(dateText) {
    if (!dateText) {
      return "-";
    }

    var date = new Date(dateText);
    if (isNaN(date.getTime())) {
      return dateText;
    }

    return date.toLocaleString();
  }

  function renderSubmissions(items) {
    if (!submissionsTbody) {
      return;
    }

    submissionsTbody.innerHTML = "";

    if (!Array.isArray(items) || items.length === 0) {
      clearSubmissionsTable("No submissions found.");
      return;
    }

    items.forEach(function (item) {
      var row = document.createElement("tr");

      var whenCell = document.createElement("td");
      whenCell.textContent = formatDate(item.created_at);

      var nameCell = document.createElement("td");
      nameCell.textContent = item.name || "-";

      var emailCell = document.createElement("td");
      emailCell.textContent = maskEmail(item.email || "");

      var messageCell = document.createElement("td");
      messageCell.className = "message-cell";
      messageCell.textContent = item.message || "-";

      var actionsCell = document.createElement("td");
      var deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "button-danger button-small";
      deleteBtn.textContent = "Delete";
      deleteBtn.addEventListener("click", function () {
        deleteSubmission(item.submission_id, row);
      });
      actionsCell.appendChild(deleteBtn);

      row.appendChild(whenCell);
      row.appendChild(nameCell);
      row.appendChild(emailCell);
      row.appendChild(messageCell);
      row.appendChild(actionsCell);

      submissionsTbody.appendChild(row);
    });
  }

  function maskEmail(email) {
    if (!email || email.indexOf("@") === -1) {
      return "-";
    }

    var parts = email.split("@");
    var local = parts[0];
    var domain = parts[1];
    var localVisible = local.slice(0, 2);
    var domainParts = domain.split(".");
    var domainName = domainParts[0] || "";
    var tld = domainParts.slice(1).join(".");
    var maskedLocal = localVisible + "*".repeat(Math.max(1, local.length - localVisible.length));
    var maskedDomain = (domainName.slice(0, 1) || "*") + "*".repeat(Math.max(1, domainName.length - 1));
    return maskedLocal + "@" + maskedDomain + (tld ? "." + tld : "");
  }

  function filterItemsByRange(items, rangeDays) {
    if (!Array.isArray(items) || rangeDays === "all") {
      return items || [];
    }

    var days = parseInt(rangeDays, 10);
    if (isNaN(days) || days <= 0) {
      return items;
    }

    var threshold = Date.now() - days * 24 * 60 * 60 * 1000;
    return items.filter(function (item) {
      var createdAt = new Date(item.created_at || "").getTime();
      if (isNaN(createdAt)) {
        return false;
      }
      return createdAt >= threshold;
    });
  }

  function filterItemsBySearch(items, term) {
    if (!term) {
      return items;
    }

    var lower = term.toLowerCase();
    return items.filter(function (item) {
      var name = (item.name || "").toLowerCase();
      var email = (item.email || "").toLowerCase();
      return name.indexOf(lower) !== -1 || email.indexOf(lower) !== -1;
    });
  }

  function mergeUniqueSubmissions(existing, incoming) {
    var byId = {};
    var merged = [];

    existing.concat(incoming).forEach(function (item) {
      var id = item && item.submission_id ? item.submission_id : "";
      var key = id || (item.email || "") + "|" + (item.created_at || "") + "|" + (item.message || "");
      if (!byId[key]) {
        byId[key] = true;
        merged.push(item);
      }
    });

    return merged;
  }

  function updateLoadMoreButton() {
    if (!adminLoadMoreButton) {
      return;
    }

    adminLoadMoreButton.disabled = !adminState.nextCursor;
  }

  function readFormData() {
    return {
      name: form.name.value.trim(),
      email: form.email.value.trim(),
      message: form.message.value.trim(),
      website: form.website ? form.website.value.trim() : ""
    };
  }

  function validate(payload) {
    if (!payload.name || !payload.email || !payload.message) {
      return "Please fill in name, email, and message.";
    }

    if (payload.website) {
      return "Unable to submit the form right now.";
    }

    return "";
  }

  async function submitForm(event) {
    event.preventDefault();

    if (!isConfiguredApiUrl(config.contactApiUrl)) {
      setStatus("The frontend is not configured with an API endpoint yet.", "error");
      return;
    }

    var payload = readFormData();
    var validationError = validate(payload);
    if (validationError) {
      setStatus(validationError, "error");
      return;
    }

    submitButton.disabled = true;
    setStatus("Sending your message...");

    try {
      var response = await fetch(config.contactApiUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });

      var data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || "Unexpected request error.");
      }

      form.reset();
      setStatus(data.message || "Message sent successfully.", "success");
    } catch (error) {
      setStatus(error.message || "Unable to submit the form right now.", "error");
    } finally {
      submitButton.disabled = false;
    }
  }

  function readAdminForm() {
    return {
      token: adminForm.adminToken.value.trim(),
      limit: adminForm.limit.value.trim() || "20",
      rangeDays: adminForm.rangeDays.value || "all"
    };
  }

  async function fetchSubmissionsPage(token, limit, cursor) {
    var submissionsUrl = getSubmissionsApiUrl();
    var requestUrl = submissionsUrl + "?limit=" + encodeURIComponent(limit);
    if (cursor) {
      requestUrl += "&cursor=" + encodeURIComponent(cursor);
    }

    var response = await fetch(requestUrl, {
      method: "GET",
      headers: {
        "x-admin-token": token
      }
    });

    var data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Unable to load submissions.");
    }

    return data;
  }

  function refreshAdminTable() {
    var filteredItems = filterItemsByRange(adminState.items, adminState.rangeDays);
    filteredItems = filterItemsBySearch(filteredItems, adminState.searchTerm);
    renderSubmissions(filteredItems);
    updateLoadMoreButton();
    setAdminStatus("Loaded " + filteredItems.length + " submission(s) in current filter.", "success");
    if (adminExportButton) {
      adminExportButton.disabled = filteredItems.length === 0;
    }
  }

  async function loadSubmissions(event) {
    event.preventDefault();

    var submissionsUrl = getSubmissionsApiUrl();
    if (!submissionsUrl) {
      setAdminStatus("The frontend is not configured with an API endpoint yet.", "error");
      return;
    }

    var adminData = readAdminForm();
    if (!adminData.token) {
      setAdminStatus("Admin token is required.", "error");
      return;
    }

    adminLoadButton.disabled = true;
    setAdminStatus("Loading recent submissions...");

    try {
      var data = await fetchSubmissionsPage(adminData.token, adminData.limit, "");
      adminState.token = adminData.token;
      adminState.limit = adminData.limit;
      adminState.rangeDays = adminData.rangeDays;
      adminState.searchTerm = adminSearchInput ? adminSearchInput.value.trim() : "";
      adminState.nextCursor = data.next_cursor || "";
      adminState.items = Array.isArray(data.items) ? data.items : [];
      refreshAdminTable();
    } catch (error) {
      clearSubmissionsTable("No submissions loaded yet.");
      adminState.nextCursor = "";
      adminState.items = [];
      updateLoadMoreButton();
      setAdminStatus(error.message || "Unable to load submissions.", "error");
    } finally {
      adminLoadButton.disabled = false;
    }
  }

  async function deleteSubmission(submissionId, rowElement) {
    if (!submissionId || !adminState.token) {
      return;
    }

    var submissionsUrl = getSubmissionsApiUrl();
    if (!submissionsUrl) {
      return;
    }

    if (!window.confirm("Delete this submission? This action cannot be undone.")) {
      return;
    }

    try {
      var response = await fetch(submissionsUrl + "/" + encodeURIComponent(submissionId), {
        method: "DELETE",
        headers: { "x-admin-token": adminState.token }
      });

      if (!response.ok) {
        var data = await response.json();
        throw new Error(data.error || "Unable to delete submission.");
      }

      adminState.items = adminState.items.filter(function (item) {
        return item.submission_id !== submissionId;
      });

      if (rowElement && rowElement.parentNode) {
        rowElement.parentNode.removeChild(rowElement);
      }

      setAdminStatus("Submission deleted.", "success");
      if (adminExportButton) {
        adminExportButton.disabled = adminState.items.length === 0;
      }
    } catch (error) {
      setAdminStatus(error.message || "Unable to delete submission.", "error");
    }
  }

  function exportCsv() {
    var filteredItems = filterItemsByRange(adminState.items, adminState.rangeDays);
    filteredItems = filterItemsBySearch(filteredItems, adminState.searchTerm);

    if (filteredItems.length === 0) {
      return;
    }

    var header = ["When", "Name", "Email", "Message"];
    var rows = filteredItems.map(function (item) {
      return [
        item.created_at || "",
        item.name || "",
        item.email || "",
        (item.message || "").replace(/\n/g, " ")
      ].map(function (cell) {
        return '"' + String(cell).replace(/"/g, '""') + '"';
      }).join(",");
    });

    var csv = [header.join(",")].concat(rows).join("\r\n");
    var blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "submissions.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function loadMoreSubmissions() {
    if (!adminState.nextCursor || !adminState.token) {
      return;
    }

    adminLoadMoreButton.disabled = true;
    setAdminStatus("Loading more submissions...");

    try {
      var data = await fetchSubmissionsPage(adminState.token, adminState.limit, adminState.nextCursor);
      var incoming = Array.isArray(data.items) ? data.items : [];
      adminState.items = mergeUniqueSubmissions(adminState.items, incoming);
      adminState.nextCursor = data.next_cursor || "";
      refreshAdminTable();
    } catch (error) {
      updateLoadMoreButton();
      setAdminStatus(error.message || "Unable to load more submissions.", "error");
    }
  }

  form.addEventListener("submit", submitForm);

  if (adminForm) {
    adminForm.addEventListener("submit", loadSubmissions);
  }

  if (adminLoadMoreButton) {
    adminLoadMoreButton.addEventListener("click", loadMoreSubmissions);
  }

  if (adminSearchInput) {
    adminSearchInput.addEventListener("input", function () {
      adminState.searchTerm = adminSearchInput.value.trim();
      if (adminState.items.length > 0) {
        refreshAdminTable();
      }
    });
  }

  if (adminExportButton) {
    adminExportButton.addEventListener("click", exportCsv);
  }
})();
