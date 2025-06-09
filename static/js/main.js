document.addEventListener('DOMContentLoaded', function() {
    const callBtn = document.getElementById('callBtn');
    const formContainer = document.getElementById('formContainer');
    const closeBtn = document.getElementById('closeBtn');
    const callForm = document.getElementById('callForm');

    // Toggle form visibility
    callBtn.addEventListener('click', () => {
        formContainer.classList.add('active');
    });

    closeBtn.addEventListener('click', () => {
        formContainer.classList.remove('active');
    });

    // Form submission
    callForm.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const submitButton = this.querySelector('button[type="submit"]');
        submitButton.disabled = true;
        submitButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Processing...';
        
        const formData = new FormData(this);
        
        fetch('/submit', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            const responseDiv = document.getElementById('responseMessage');
            
            if (data.success) {
                responseDiv.className = "alert alert-success show";
                responseDiv.innerHTML = `<strong>Success!</strong> ${data.message}`;
                callForm.reset();
                
                // Auto close form after success
                setTimeout(() => {
                    formContainer.classList.remove('active');
                }, 3000);
            } else {
                responseDiv.className = "alert alert-danger show";
                responseDiv.innerHTML = `<strong>Error!</strong> ${data.message}`;
            }
            
            submitButton.disabled = false;
            submitButton.innerHTML = '<i class="fas fa-phone-alt me-2"></i>Request a Call';
        })
        .catch(error => {
            console.error('Error:', error);
            const responseDiv = document.getElementById('responseMessage');
            responseDiv.className = "alert alert-danger show";
            responseDiv.innerHTML = '<strong>Error!</strong> Something went wrong. Please try again later.';
            
            submitButton.disabled = false;
            submitButton.innerHTML = '<i class="fas fa-phone-alt me-2"></i>Request a Call';
        });
    });
});