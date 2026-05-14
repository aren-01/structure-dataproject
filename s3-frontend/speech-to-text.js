document.addEventListener("DOMContentLoaded", () => {
  const inputBox = document.getElementById("inputBox");
  const micBtn = document.getElementById("micBtn");

  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!inputBox || !micBtn) {
    console.warn("inputBox or micBtn not found.");
    return;
  }

  if (!SpeechRecognition) {
    micBtn.disabled = true;
    micBtn.textContent = "Mic not supported";
    return;
  }

  const recognition = new SpeechRecognition();

  recognition.lang = document.documentElement.lang || navigator.language || "en-US";
  recognition.continuous = true;
  recognition.interimResults = true;

  let isListening = false;
  let baseText = "";

  function startListening() {
    baseText = inputBox.value ? inputBox.value.trimEnd() + " " : "";

    try {
      recognition.start();
      isListening = true;
      micBtn.textContent = "Stop talking";
      micBtn.classList.add("listening");
    } catch (error) {
      console.warn("Could not start speech recognition:", error);
    }
  }

  function stopListening() {
    isListening = false;
    recognition.stop();
    micBtn.textContent = "Start talking";
    micBtn.classList.remove("listening");
  }

  micBtn.addEventListener("click", () => {
    if (isListening) {
      stopListening();
    } else {
      startListening();
    }
  });

  recognition.onresult = (event) => {
    let finalText = "";
    let interimText = "";

    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;

      if (event.results[i].isFinal) {
        finalText += transcript + " ";
      } else {
        interimText += transcript;
      }
    }

    if (finalText) {
      baseText += finalText;
    }

    inputBox.value = baseText + interimText;
  };

  recognition.onerror = (event) => {
    console.error("Speech recognition error:", event.error);

    isListening = false;
    micBtn.textContent = "Start talking";
    micBtn.classList.remove("listening");
  };

  recognition.onend = () => {
    if (isListening) {
      try {
        recognition.start();
      } catch (error) {
        console.warn("Could not restart speech recognition:", error);
      }
    } else {
      micBtn.textContent = "Start talking";
      micBtn.classList.remove("listening");
    }
  };
});
