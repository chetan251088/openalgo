# Browser Setup for Dual Instances

## Quick Fix: Use Separate Browser Profiles

Since both Kotak (5000) and Dhan (5001) run on `127.0.0.1`, cookies can conflict.

### Option 1: Use Different Browsers
- **Kotak**: Open in Chrome → http://127.0.0.1:5000
- **Dhan**: Open in Edge/Firefox → http://127.0.0.1:5001

### Option 2: Use Chrome Incognito for Second Instance
1. **Kotak**: Normal Chrome window → http://127.0.0.1:5000
2. **Dhan**: Incognito window (Ctrl+Shift+N) → http://127.0.0.1:5001

### Option 3: Use Chrome Profiles
1. Create new Chrome profile:
   - Click profile icon (top right)
   - Click "Add"
   - Name it "Dhan Trading"
2. Use profiles:
   - **Kotak**: Default profile → http://127.0.0.1:5000
   - **Dhan**: "Dhan Trading" profile → http://127.0.0.1:5001

## Current Issue: Redirect to Wrong Port

After Dhan OAuth login, you're redirected to port 5000 instead of 5001.

### Why This Happens
The redirect URL in Dhan's developer portal is likely set to:
```
http://127.0.0.1:5000/dhan/callback
```

But it should be:
```
http://127.0.0.1:5001/dhan/callback
```

### Fix: Update Dhan Developer Portal

1. **Login to Dhan Developer Portal**
   - URL: https://dhan.co (or developer portal URL)

2. **Navigate to Your App Settings**
   - Find your registered application
   - Look for "Redirect URL" or "Callback URL" field

3. **Add Port 5001 Redirect URL**
   - Current: `http://127.0.0.1:5000/dhan/callback`
   - Add new: `http://127.0.0.1:5001/dhan/callback`
   - Save changes

4. **Use Correct URL When Logging In**
   - Make sure you're accessing `http://127.0.0.1:5001` (not 5000)
   - Clear browser cookies before login
   - Complete OAuth flow

## Temporary Workaround (Until Developer Portal Updated)

If you can't update Dhan's developer portal immediately:

1. **Test Dhan with Kotak Stopped**:
   ```cmd
   # Stop Kotak instance (close terminal)
   # Run only Dhan instance
   run_dhan.bat
   ```

2. **After Dhan login completes, start Kotak**:
   ```cmd
   run_kotak.bat
   ```

This way there's no port conflict during OAuth.

## Verify Configuration

Check your current .env settings:
```cmd
# Should show Dhan configuration when run_dhan.bat is active
type .env | findstr "FLASK_PORT REDIRECT_URL"
```

Expected output:
```
FLASK_PORT='5001'
REDIRECT_URL = 'http://127.0.0.1:5001/dhan/callback'
```
