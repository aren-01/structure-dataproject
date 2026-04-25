
    const AUTH_CONFIG = {
      region: "__COGNITO_REGION__",
      userPoolId: "__COGNITO_USER_POOL_ID__",
      clientId: "__COGNITO_CLIENT_ID__"
    };
// https://github.com/awsdocs/aws-doc-sdk-examples/blob/main/javascriptv3/example_code/cross-services/textract-react/src/index.js
// https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-integrating-user-pools-with-identity-pools.html

    const STORAGE_KEYS = {
      idToken: "app_id_token",
      accessToken: "app_access_token",
      refreshToken: "app_refresh_token",
      username: "app_username",
      name: "app_name",
      signedIn: "app_signed_in"
    };

    const tabButtons = document.querySelectorAll(".tab-btn");
    const panels = {
      login: document.getElementById("loginPanel"),
      signup: document.getElementById("signupPanel"),
      confirm: document.getElementById("confirmPanel"),
      forgot: document.getElementById("forgotPanel"),
      reset: document.getElementById("resetPanel")
    };
    const status = document.getElementById("status");

    function showStatus(message, type = "info") {
      status.textContent = message;
      status.className = "";
      if (type === "success") status.classList.add("status-success");
      if (type === "error") status.classList.add("status-error");
    }

    function ensureConfig() {
      const values = Object.values(AUTH_CONFIG);
      const invalid = values.some((value) => !value || value.startsWith("__COGNITO_"));
      if (invalid) {
        showStatus("Cognito configuration is missing. Inject region, user pool ID, and client ID during deployment.", "error");
        return false;
      }
      return true;
    }

    function setTab(tabName) {
      tabButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tabName));
      Object.entries(panels).forEach(([name, panel]) => panel.classList.toggle("active", name === tabName));
      showStatus("");
    }

    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => setTab(btn.dataset.tab));
    });

    document.getElementById("forgotLink").addEventListener("click", () => {
      const email = document.getElementById("loginUser").value.trim();
      if (email) document.getElementById("forgotEmail").value = email;
      setTab("forgot");
    });

    function getUserPool() {
      return new AmazonCognitoIdentity.CognitoUserPool({
        UserPoolId: AUTH_CONFIG.userPoolId,
        ClientId: AUTH_CONFIG.clientId
      });
    }

    function getCognitoUser(email) {
      return new AmazonCognitoIdentity.CognitoUser({
        Username: email,
        Pool: getUserPool()
      });
    }

    function storeSession(result, username, displayName = "") {
      localStorage.setItem(STORAGE_KEYS.idToken, result.getIdToken().getJwtToken());
      localStorage.setItem(STORAGE_KEYS.accessToken, result.getAccessToken().getJwtToken());
      localStorage.setItem(STORAGE_KEYS.refreshToken, result.getRefreshToken().getToken());
      localStorage.setItem(STORAGE_KEYS.username, username || "");
      localStorage.setItem(STORAGE_KEYS.name, displayName || "");
      localStorage.setItem(STORAGE_KEYS.signedIn, "true");
    }

    function decodeJwt(token) {
      try {
        const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
        return JSON.parse(atob(base64));
      } catch (error) {
        return null;
      }
    }

    function redirectIfAlreadySignedIn() {
      const idToken = localStorage.getItem(STORAGE_KEYS.idToken);
      if (!idToken) return;
      const payload = decodeJwt(idToken);
      if (payload && payload.exp * 1000 > Date.now()) {
        window.location.href = "/";
      }
    }

    redirectIfAlreadySignedIn();

    document.getElementById("signupBtn").addEventListener("click", () => {
      if (!ensureConfig()) return;

      const name = document.getElementById("signupName").value.trim();
      const email = document.getElementById("signupEmail").value.trim().toLowerCase();
      const password = document.getElementById("signupPass").value;
      const confirmPassword = document.getElementById("signupPassConfirm").value;

      if (!name || !email || !password || !confirmPassword) {
        showStatus("Fill all Sign Up fields.", "error");
        return;
      }

      if (password !== confirmPassword) {
        showStatus("Passwords do not match.", "error");
        return;
      }

      const attributeList = [
        new AmazonCognitoIdentity.CognitoUserAttribute({ Name: "email", Value: email }),
        new AmazonCognitoIdentity.CognitoUserAttribute({ Name: "name", Value: name })
      ];

      getUserPool().signUp(email, password, attributeList, null, (err) => {
        if (err) {
          showStatus(err.message || "Unable to create your account.", "error");
          return;
        }

        document.getElementById("confirmEmail").value = email;
        showStatus("Account created. Enter the verification code sent to your email.", "success");
        setTab("confirm");
      });
    });

    document.getElementById("confirmBtn").addEventListener("click", () => {
      if (!ensureConfig()) return;

      const email = document.getElementById("confirmEmail").value.trim().toLowerCase();
      const code = document.getElementById("confirmCode").value.trim();

      if (!email || !code) {
        showStatus("Enter your email and verification code.", "error");
        return;
      }

      getCognitoUser(email).confirmRegistration(code, true, (err) => {
        if (err) {
          showStatus(err.message || "Unable to confirm account.", "error");
          return;
        }

        document.getElementById("loginUser").value = email;
        showStatus("Your account is confirmed. You can log in now.", "success");
        setTab("login");
      });
    });

    document.getElementById("resendBtn").addEventListener("click", () => {
      if (!ensureConfig()) return;

      const email = document.getElementById("confirmEmail").value.trim().toLowerCase();
      if (!email) {
        showStatus("Enter your email to resend the code.", "error");
        return;
      }

      getCognitoUser(email).resendConfirmationCode((err) => {
        if (err) {
          showStatus(err.message || "Unable to resend code.", "error");
          return;
        }
        showStatus("Verification code sent again.", "success");
      });
    });

    document.getElementById("loginBtn").addEventListener("click", () => {
      if (!ensureConfig()) return;

      const username = document.getElementById("loginUser").value.trim().toLowerCase();
      const password = document.getElementById("loginPass").value;
      if (!username || !password) {
        showStatus("Enter email and password.", "error");
        return;
      }

      const authenticationData = new AmazonCognitoIdentity.AuthenticationDetails({
        Username: username,
        Password: password
      });

      const cognitoUser = getCognitoUser(username);
      cognitoUser.authenticateUser(authenticationData, {
        onSuccess: (result) => {
          const payload = decodeJwt(result.getIdToken().getJwtToken()) || {};
          const displayName = payload.name || payload.email || username;
          storeSession(result, username, displayName);
          showStatus("Login successful. Redirecting...", "success");
          window.location.href = "/";
        },
        onFailure: (err) => {
          showStatus(err.message || "Login failed.", "error");
        }
      });
    });

    document.getElementById("forgotBtn").addEventListener("click", () => {
      if (!ensureConfig()) return;

      const email = document.getElementById("forgotEmail").value.trim().toLowerCase();
      if (!email) {
        showStatus("Enter your email to continue.", "error");
        return;
      }

      getCognitoUser(email).forgotPassword({
        onSuccess: () => {
          document.getElementById("resetEmail").value = email;
          showStatus("Reset code sent. Enter it below with your new password.", "success");
          setTab("reset");
        },
        onFailure: (err) => {
          showStatus(err.message || "Unable to start password reset.", "error");
        },
        inputVerificationCode: () => {
          document.getElementById("resetEmail").value = email;
          showStatus("Reset code sent. Enter it below with your new password.", "success");
          setTab("reset");
        }
      });
    });

    document.getElementById("resetBtn").addEventListener("click", () => {
      if (!ensureConfig()) return;

      const email = document.getElementById("resetEmail").value.trim().toLowerCase();
      const code = document.getElementById("resetCode").value.trim();
      const newPassword = document.getElementById("resetPass").value;

      if (!email || !code || !newPassword) {
        showStatus("Enter your email, reset code, and new password.", "error");
        return;
      }

      getCognitoUser(email).confirmPassword(code, newPassword, {
        onSuccess: () => {
          document.getElementById("loginUser").value = email;
          showStatus("Password updated. You can log in now.", "success");
          setTab("login");
        },
        onFailure: (err) => {
          showStatus(err.message || "Unable to reset password.", "error");
        }
      });
    });
