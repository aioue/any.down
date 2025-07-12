# Any.down

Download your Any.do tasks to a local directory. Raw JSON and markdown.

## 🙏 Acknowledgments

This project is created as a tribute to Any.do's excellent task management service.

The optimization techniques are inspired by Any.do's own efficient web implementation, ensuring this client remains respectful of their infrastructure while providing useful backup capabilities that [do not currently exist on the official site](https://support.any.do/en/articles/8635961-printing-and-exporting-items).

## 🌟 Key Features

- **🛡️ Server-Friendly**: Designed to minimize impact on Any.do's infrastructure by copying patterns from their website (incremental sync reduces server load by 80-99%)
- **🔐 Secure Authentication**: Session persistence with 2FA support
- **📊 Multiple Export Formats**: JSON and Markdown exports

## 🎯 Quick Start

### Prerequisites
- Python 3.7 or higher
- Any.do account

### 1. Setup Environment
```bash
# Clone the repository
git clone <repository-url>
cd anydo-api

# Run the setup script (creates venv and installs dependencies)
python setup.py
```

### 2. Activate Virtual Environment
```bash
# On macOS/Linux
source venv/bin/activate

# On Windows
venv\Scripts\activate
```

### 3. Run the Application
```bash
# First run will prompt to create config.json
python anydown.py
```

The script will automatically:
- ✅ Detect if `config.json` exists
- 🔧 Offer to create one if missing
- 🔐 Prompt for your Any.do credentials
- 💾 Save configuration securely
- 📋 Sync and display your tasks

## 🔧 Configuration

The application will create a `config.json` file on first run:

```json
{
  "email": "your@email.com",
  "password": "your_password",
  "save_raw_data": true,
  "auto_export": true,
  "text_wrap_width": 80
}
```

**Security Note**: `config.json` is automatically added to `.gitignore` for protection.

## 🍪 Manual Session Setup (Alternative)

If you encounter login issues or prefer to use existing browser sessions, you can manually create a `session.json` file using browser developer tools:

### When to Use This Method
- Login authentication failures
- 2FA/MFA complications
- Already logged into Any.do in your browser
- Debugging authentication issues

### Step-by-Step Instructions

1. **Open Any.do in your browser** and ensure you're logged in
2. **Open Developer Tools** (F12 or right-click → Inspect)
3. **Go to the Application/Storage tab**
4. **Navigate to Cookies** → `https://any.do`
5. **Find the authentication cookie** (usually `SPRING_SECURITY_REMEMBER_ME_COOKIE`)
6. **Copy the cookie value**

### Create session.json File

Create a `session.json` file in your project root with the following structure (use `session.json.example` as a template):

```json
{
  "cookies": [
    {
      "name": "SPRING_SECURITY_REMEMBER_ME_COOKIE",
      "value": "YOUR_COPIED_COOKIE_VALUE_HERE",
      "domain": ".any.do",
      "path": "/"
    }
  ],
  "user_info": {
    "email": "your@email.com",
    "timezone": "Your/Timezone",
    "isPremium": false
  },
  "saved_at": "2025-01-01T00:00:00.000000",
  "last_data_hash": null,
  "last_pretty_hash": null,
  "last_sync_timestamp": 0
}
```

### Getting Additional User Info (Optional)

To populate the `user_info` section:

1. **In Developer Tools**, go to the **Network tab**
2. **Refresh the Any.do page**
3. **Look for API calls** to endpoints like `/me` or `/user`
4. **Copy relevant user data** from the response

**Note**: You can start with minimal user info - the client will update it during sync.

### Security Considerations

- **Keep session.json private** - it contains authentication data
- **Cookie expiration** - Sessions may expire and need refreshing
- **Don't commit session.json** to version control (it's in `.gitignore`)
- **Use session.json.example** as a template with sanitized placeholder values

## 🚀 Performance Optimization

This client implements intelligent sync strategies that dramatically reduce server load:

### Smart Sync Modes
- **Default**: Incremental sync with automatic fallback
- **Incremental**: Downloads only changes since last sync
- **Full**: Downloads all tasks (when needed)

### Performance Benefit Example
```
Before: 2.5MB download (every sync)
After:  15KB download (incremental sync)
Reduction: 99.4% less server load
```

### Command Line Options
```bash
# Smart sync (recommended)
python anydown.py

# Force full sync
python anydown.py --full-sync

# Incremental sync only
python anydown.py --incremental-only

# Force export even if no changes detected
python anydown.py --force
```

## 📊 Export Formats

The client generates multiple export formats:

### File Structure
```
outputs/
├── raw-json/          # Complete API responses
└── markdown/          # Formatted tables and lists
```

### Export Features
- **Change Detection**: Only creates new files when data changes
- **Timestamped Files**: Organized by date and time
- **Smart Markdown Generation**: Clean tables with task hierarchies
- **Nested Subtasks**: Properly organized task hierarchies

## 🔗 API Usage

### Basic Usage
```python
from anydo_client import AnyDoClient

client = AnyDoClient()
client.login("your@email.com", "your_password")

# Smart sync (automatic optimization)
tasks = client.get_tasks()

# Display task summary
client.print_tasks_summary()
```

### Advanced Usage
```python
# Force full sync
tasks = client.get_tasks_full()

# Incremental sync only
tasks = client.get_tasks_incremental()

# Get simplified task list
simple_tasks = client.get_simple_tasks()

# Get lists/categories
lists = client.get_lists()
```

## 🛠️ Development

### Running Tests
```bash
# Run all tests
python run_tests.py

# Or use pytest directly
python -m pytest tests/ -v
```

### Project Structure
```
anydo-api/
├── anydown.py          # Main CLI application
├── anydo_client.py     # Core client library
├── debug_login.py      # Login troubleshooting tool
├── setup.py            # Setup script
├── requirements.txt    # Dependencies
├── tests/              # Test suite
├── outputs/            # Generated exports
└── config.json         # Your credentials (auto-created)
```

## 🔒 Security & Privacy

- **Local Storage**: All data stays on your machine
- **Session Persistence**: Reduces authentication requests
- **Secure Config**: Configuration files are gitignored
- **2FA Support**: Interactive prompts for verification codes

## 🌐 Technical Details

### Sync Optimization
The client analyzes Any.do's own website patterns and implements:
- **Incremental Updates**: Uses `updatedSince` parameter
- **Timestamp Tracking**: Maintains last sync state
- **Graceful Fallback**: Handles failures transparently
- **Session Management**: Persistent authentication

### API Endpoints
```
Authentication: https://sm-prod4.any.do/login
Background Sync: https://sm-prod4.any.do/api/v14/me/bg_sync
Sync Results: https://sm-prod4.any.do/me/bg_sync_result/{task_id}
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Submit a pull request

## 📝 License

MIT License - See LICENSE file for details.

---

*Made with ❤️ for the Any.do community*
