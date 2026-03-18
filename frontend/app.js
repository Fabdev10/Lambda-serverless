(function () {
  var form = document.getElementById("contact-form");
  var submitButton = document.getElementById("submit-button");
  var statusMessage = document.getElementById("status-message");
  var config = window.APP_CONFIG || {};

  function setStatus(message, type) {
    statusMessage.textContent = message;
    statusMessage.className = "status-message";
    if (type) {
      statusMessage.classList.add("is-" + type);
    }
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

    if (!config.contactApiUrl || config.contactApiUrl.indexOf("__CONTACT_API_URL__") !== -1) {
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

  form.addEventListener("submit", submitForm);
})();
