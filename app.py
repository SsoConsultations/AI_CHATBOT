import streamlit as st
import pandas as pd
import io
import os
from openai import OpenAI
from openai import AuthenticationError, APIConnectionError, RateLimitError, APIStatusError # Import specific OpenAI errors
import requests # Import requests for potential timeout errors
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION_START
from docx.oxml.ns import qn # For setting table style
from docx.oxml import OxmlElement # For setting table style
import matplotlib.pyplot as plt
import seaborn as sns
import re # For parsing graph requests and markdown bolding
from scipy import stats # For statistical tests (including shapiro)
import numpy as np # For numerical operations, especially for ANOVA SS calculations

# --- Configuration and Secrets ---
# IMPORTANT: Create a .streamlit/secrets.toml file in your project root
# and add your OpenAI API key and login credentials there.
# Example secrets.toml structure:
# [openai]
# api_key = "sk-proj-your_openai_api_key_here"
#
# [credentials]
# user1 = "User1@123"
# user2 = "User2@123"
# "ssoconsultants14@gmail.com" = "Sso@122" # Corrected password for ssoconsultants14@gmail.com


# Load secrets
try:
    OPENAI_API_KEY = st.secrets["openai"]["api_key"] # Accessing the api_key from the [openai] section
    LOGIN_USERS = st.secrets["credentials"]          # Accessing the users from the [credentials] section
except KeyError as e:
    st.error(f"Secret not found: {e}. Please ensure your .streamlit/secrets.toml file is correctly configured with [openai] and [credentials] sections.")
    st.stop() # Stop the app if secrets is missing

# Initialize OpenAI client (will be initialized after API key check)
client = None 

# --- Session State Initialization ---
# Initialize session state variables if they don't exist
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'current_username' not in st.session_state:
    st.session_state['current_username'] = None
if 'df' not in st.session_state:
    st.session_state['df'] = None
if 'data_summary_text' not in st.session_state: # Text summary for AI
    st.session_state['data_summary_text'] = ""
if 'data_summary_table' not in st.session_state: # Structured data for report table
    st.session_state['data_summary_table'] = []
if 'messages' not in st.session_state:
    st.session_state['messages'] = []
if 'report_content' not in st.session_state:
    st.session_state['report_content'] = [] # List to store report sections (structured)
if 'user_goal' not in st.session_state:
    st.session_state['user_goal'] = "Not specified"
if 'uploaded_file_name' not in st.session_state: # To track if a new file is uploaded
    st.session_state['uploaded_file_name'] = None
if 'openai_client_initialized' not in st.session_state:
    st.session_state['openai_client_initialized'] = False
if 'openai_client' not in st.session_state: # New: Store OpenAI client instance here
    st.session_state['openai_client'] = None
if 'debug_logs' not in st.session_state: # New: For in-app debug logs
    st.session_state['debug_logs'] = []

# --- Helper Functions ---

# Function to append debug messages to session state
def append_debug_log(message):
    st.session_state['debug_logs'].append(message)

def check_password():
    """
    Checks if the entered username and password match any of the ones in st.secrets['credentials'].
    This uses plain-text password comparison as per user's request,
    acknowledging the security implications.
    """
    if st.session_state['logged_in']:
        return True

    st.title("Login to Data Preprocessing Assistant")
    st.markdown("---")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit_button = st.form_submit_button("Login")

        if submit_button:
            # Check if the entered username exists in our stored users
            if username in LOGIN_USERS:
                # Compare the entered password with the stored plain-text password
                if password == LOGIN_USERS[username]:
                    st.session_state['logged_in'] = True
                    st.session_state['current_username'] = username # Store current logged-in username
                    st.rerun() # Rerun to switch to the main app
                else:
                    st.error("Invalid username or password.")
            else:
                st.error("Invalid username or password.")
    return False

def check_openai_api_key():
    """
    Attempts a simple OpenAI API call to verify the API key and store the client.
    Returns True if successful, False otherwise.
    """
    if st.session_state['openai_client_initialized'] and st.session_state['openai_client'] is not None:
        return True # Already checked and initialized

    try:
        # Initialize client and store in session state
        st.session_state['openai_client'] = OpenAI(api_key=OPENAI_API_KEY)
        
        # Attempt a simple call to verify the key
        response = st.session_state['openai_client'].chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=5,
            stream=False, # Do not stream for this check
            timeout=10 # Add a timeout for the API call
        )
        st.session_state['openai_client_initialized'] = True
        st.success("OpenAI API key verified successfully! AI features are enabled.")
        return True
    except AuthenticationError:
        st.error("OpenAI API Key is invalid. Please check your .streamlit/secrets.toml file.")
        st.session_state['openai_client'] = None # Clear client on failure
        return False
    except APIConnectionError as e:
        st.error(f"Could not connect to OpenAI API: {e}. Please check your internet connection and firewall settings.")
        st.session_state['openai_client'] = None # Clear client on failure
        return False
    except RateLimitError:
        st.error("OpenAI API rate limit exceeded. Please try again later or check your OpenAI usage.")
        st.session_state['openai_client'] = None # Clear client on failure
        return False
    except requests.exceptions.Timeout:
        st.error("OpenAI API connection timed out during key verification. Please try again.")
        st.session_state['openai_client'] = None
        return False
    except APIStatusError as e: # Catch API specific status errors
        st.error(f"OpenAI API returned an error status: {e.status_code} - {e.response}")
        st.session_state['openai_client'] = None
        return False
    except Exception as e:
        st.error(f"An unexpected error occurred while checking OpenAI API key: {e}")
        st.session_state['openai_client'] = None # Clear client on failure
        return False


def get_data_summary(df):
    """
    Generates a comprehensive summary of the DataFrame's characteristics for AI and report.
    Returns (summary_text, column_details_for_table).
    """
    summary_text = []
    column_details_for_table = [] # For the report table

    summary_text.append(f"Dataset Overview:\n")
    summary_text.append(f"- Number of rows: {df.shape[0]}")
    summary_text.append(f"- Number of columns: {df.shape[1]}")
    summary_text.append(f"- Total duplicate rows: {df.duplicated().sum()}\n")

    column_details_for_table.append(["Column Name", "Data Type", "Missing %", "Stats Summary"]) # Table Header

    # Add a general heading for column details in the text summary
    summary_text.append("Column Details:\n")

    for col in df.columns:
        dtype = df[col].dtype
        missing_percent = df[col].isnull().sum() / len(df) * 100
        
        col_summary_text_parts = []
        col_table_summary = ""

        # For text summary
        col_summary_text_parts.append(f"  - Column '{col}':")
        col_summary_text_parts.append(f"    - Data Type: {dtype}")
        col_summary_text_parts.append(f"    - Missing Values: {missing_percent:.2f}%")

        if pd.api.types.is_numeric_dtype(df[col]):
            desc = df[col].describe()
            col_summary_text_parts.append(f"    - Numerical Stats:")
            col_summary_text_parts.append(f"      - Mean: {desc['mean']:.2f}")
            col_summary_text_parts.append(f"      - Median: {df[col].median():.2f}")
            col_summary_text_parts.append(f"      - Std Dev: {desc['std']:.2f}")
            col_summary_text_parts.append(f"      - Min: {desc['min']:.2f}")
            col_summary_text_parts.append(f"      - Max: {desc['max']:.2f}")
            col_summary_text_parts.append(f"      - 25th Percentile: {desc['25%']:.2f}")
            col_summary_text_parts.append(f"      - 75th Percentile: {desc['75%']:.2f}")
            col_summary_text_parts.append(f"      - Skewness: {df[col].skew():.2f}")
            col_summary_text_parts.append(f"      - Kurtosis: {df[col].kurt():.2f}")
            col_table_summary = (
                f"Mean: {desc['mean']:.2f}, Median: {df[col].median():.2f}, "
                f"Skew: {df[col].skew():.2f}, Missing: {missing_percent:.2f}%"
            )
        elif pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
            unique_count = df[col].nunique()
            top_values = df[col].value_counts().head(3) # Limit to top 3 for table summary
            col_summary_text_parts.append(f"    - Categorical Stats:")
            col_summary_text_parts.append(f"      - Unique Values (Cardinality): {unique_count}")
            if not top_values.empty:
                top_vals_str = ", ".join([f"'{val}': {count}" for val, count in top_values.items()])
                col_summary_text_parts.append(f"      - Top 3 Values and Counts: {top_vals_str}")
                col_table_summary = f"Unique: {unique_count}, Top: {top_vals_str}, Missing: {missing_percent:.2f}%"
            else:
                col_table_summary = f"Unique: {unique_count}, Missing: {missing_percent:.2f}%"
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            col_summary_text_parts.append(f"    - Date/Time Stats:")
            col_summary_text_parts.append(f"      - Min Date: {df[col].min()}")
            col_summary_text_parts.append(f"      - Max Date: {df[col].max()}")
            col_table_summary = (
                f"Min Date: {df[col].min().strftime('%Y-%m-%d')}, "
                f"Max Date: {df[col].max().strftime('%Y-%m-%d')}, Missing: {missing_percent:.2f}%"
            )
        
        summary_text.append("\n".join(col_summary_text_parts))
        summary_text.append("") # Add a blank line for readability between columns in text summary
        column_details_for_table.append([col, str(dtype), f"{missing_percent:.2f}%", col_table_summary])

    return "\n".join(summary_text), column_details_for_table

def generate_openai_response(prompt, model="gpt-3.5-turbo"):
    """
    Sends a prompt to the OpenAI API and returns the response.
    Explicitly instructs the model NOT to provide Python code snippets or markdown formatting.
    """
    # Retrieve client from session state
    client_instance = st.session_state.get('openai_client')

    if client_instance is None or not st.session_state['openai_client_initialized']:
        append_debug_log("DEBUG: OpenAI client not initialized or missing.") # Debug print
        return "AI features are not enabled due to API key issues. Please check your OpenAI API key."

    try:
        # Filter messages to only include text-based content for the AI API call
        api_messages_history = []
        # Only send the last 5 relevant messages to save tokens and maintain context
        for msg in st.session_state.messages[-5:]:
            if msg["role"] in ["user", "assistant"]: # Only include user and assistant text messages
                api_messages_history.append({"role": msg["role"], "content": msg["content"]})
        
        # Add the current user prompt
        api_messages_history.append({"role": "user", "content": prompt})

        append_debug_log(f"DEBUG: Sending prompt to OpenAI (max_tokens=2000):\n{api_messages_history}\n---") # Debug print
        
        # --- NEW: More robust API call with specific error handling and timeout ---
        try:
            response = client_instance.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a data preprocessing expert. Provide clear, concise, and actionable advice. Do NOT use any markdown formatting (like bolding, italics, code blocks) in your responses. Focus on natural language explanations and interpretations. Always ask the user about their goal for the dataset if not specified, or if a graph is generated, provide an interpretation of that graph. Keep responses concise and to the point."},
                    *api_messages_history
                ],
                temperature=0.7,
                max_tokens=2000,
                top_p=1.0,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                timeout=30 # Increased timeout to 30 seconds for API calls
            )
            ai_response_content = response.choices[0].message.content
            append_debug_log(f"DEBUG: Raw OpenAI response received:\n{ai_response_content}\n---") # Debug print
            return ai_response_content
        except AuthenticationError as e:
            append_debug_log(f"DEBUG: API Call AuthenticationError: {e}") # Debug print
            st.error("OpenAI API Key is invalid during chat. Please check your .streamlit/secrets.toml file.")
            return "I'm sorry, my connection to the AI failed due to an invalid API key. Please contact support."
        except APIConnectionError as e:
            append_debug_log(f"DEBUG: API Call ConnectionError: {e}") # Debug print
            st.error(f"Could not connect to OpenAI API during chat: {e}. Please check your internet connection and firewall settings.")
            return "I'm sorry, I'm having trouble connecting to the AI. Please check your internet connection and try again."
        except RateLimitError as e:
            append_debug_log(f"DEBUG: API Call RateLimitError: {e}") # Debug print
            st.error("OpenAI API rate limit exceeded during chat. Please try again in a moment.")
            return "I'm sorry, the AI is experiencing high demand. Please try again in a moment."
        except requests.exceptions.Timeout as e: # Catch specific requests timeout
            append_debug_log(f"DEBUG: API Call Timeout: {e}") # Debug print
            st.error("OpenAI API request timed out. The server took too long to respond. Please try again.")
            return "I'm sorry, the AI took too long to respond. Please try again in a moment."
        except APIStatusError as e: # Catch API specific status errors (e.g., 4xx, 5xx from OpenAI)
            append_debug_log(f"DEBUG: API Call Status Error: {e.status_code} - {e.response}") # Debug print
            st.error(f"OpenAI API returned an error status: {e.status_code}. Please try again later.")
            return f"I'm sorry, the AI encountered an error ({e.status_code}). Please try again later."
        except Exception as e: # Catch any other unexpected errors during the API call
            append_debug_log(f"DEBUG: Unexpected Exception during API call: {e}") # Debug print
            st.error(f"An unexpected error occurred during AI API call: {e}")
            st.exception(e) # Display the full traceback in the Streamlit UI for debugging
            return "I'm sorry, an unexpected error occurred while communicating with the AI. Please try again later."

    except Exception as e: # This outer catch block is for errors *before* the API call (e.g., client not initialized)
        append_debug_log(f"DEBUG: General Exception in generate_openai_response (outer block): {e}") # Debug print
        st.error(f"An unexpected error occurred while generating the AI response: {e}")
        st.exception(e) # Display the full traceback in the Streamlit UI for debugging
        return "I'm sorry, an unexpected error occurred while generating the AI response. Please try again later."


def create_report_doc(report_data, logo_path="SsoLogo.jpg"):
    """
    Generates a Word document report from the accumulated report_data.
    """
    document = Document()

    # Add Logo to the report
    try:
        document.add_picture(logo_path, width=Inches(1.5))
        last_paragraph = document.paragraphs[-1]
        last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    except FileNotFoundError:
        document.add_paragraph("SSO Consultants Logo (Image not found)")
    except Exception as e:
        document.add_paragraph(f"Error adding logo to report: {e}")

    document.add_heading('Dataset Preprocessing & Analysis Report', level=1)
    document.add_paragraph(f"Date Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    document.add_paragraph(f"User's Stated Goal: {st.session_state['user_goal']}")
    document.add_paragraph("\n")

    for item in report_data:
        item_type = item.get("type")
        content = item.get("content")

        if item_type == "heading":
            heading = document.add_heading('', level=item.get("level", 2))
            run = heading.add_run(content)
            run.bold = True # Ensure headings are bold
        elif item_type == "text":
            # REVISED LOGIC FOR TEXT CONTENT TO HANDLE NUMBERED LISTS PROPERLY
            lines = content.split('\n')
            for line in lines:
                line = line.strip() # Remove leading/trailing whitespace
                if not line: # Skip empty lines
                    continue

                p = document.add_paragraph()
                # Check if it's a numbered list item
                numbered_list_match = re.match(r'^(\d+\.\s*)(.*)', line)
                if numbered_list_match:
                    # Add the number part (e.g., "1. ")
                    p.add_run(numbered_list_match.group(1))
                    
                    # Get the rest of the line after the number
                    rest_of_line = numbered_list_match.group(2)
                    
                    # This regex is for finding markdown bolding within the rest_of_line
                    parts = re.split(r'(\*\*.*?\*\*)', rest_of_line)
                    for part in parts:
                        if part is None or part == '':
                            continue
                        if part.startswith('**') and part.endswith('**'):
                            run = p.add_run(part[2:-2])
                            run.bold = True
                        else:
                            p.add_run(part)
                else:
                    # Not a numbered list item, just add as a regular paragraph
                    # Also check for markdown bolding in regular paragraphs
                    parts = re.split(r'(\*\*.*?\*\*)', line)
                    for part in parts:
                        if part is None or part == '':
                            continue
                        if part.startswith('**') and part.endswith('**'):
                            run = p.add_run(part[2:-2])
                            run.bold = True
                        else:
                            p.add_run(part)

        elif item_type == "table":
            # Original headers and rows from st.session_state['data_summary_table']
            original_headers = item.get("headers", []) # ['Column Name', 'Data Type', 'Missing %', 'Stats Summary']
            original_rows = item.get("rows", []) # [['ID', 'int64', '0.00%', 'Mean: ...'], ...]

            if original_headers and original_rows:
                # Add a specific sub-heading for the table
                table_heading = document.add_heading('', level=3)
                run = table_heading.add_run("Column Details Overview")
                run.bold = True

                # Define new headers for the Word table
                # We want "Column Name\n(Type)", "Missing %", "Stats Summary"
                new_table_headers = ["Column Name\n(Type)", original_headers[2], original_headers[3]]
                
                table = document.add_table(rows=1, cols=len(new_table_headers))
                table.style = 'Table Grid' # Apply a basic table style

                # Add new headers to the Word table
                hdr_cells = table.rows[0].cells
                for i, header_text in enumerate(new_table_headers):
                    hdr_cells[i].text = header_text
                    # Set header bold
                    for paragraph in hdr_cells[i].paragraphs:
                        for run in paragraph.runs:
                            run.bold = True

                # Data type short form mapping
                dtype_map = {
                    'int64': 'num',
                    'float64': 'num',
                    'object': 'str',
                    'category': 'str',
                    'datetime64[ns]': 'date', # Pandas datetime dtype often includes [ns]
                    'datetime64': 'date', # General datetime
                    'bool': 'bool' # Boolean type
                }

                # Add rows, transforming the first two columns
                for row_data in original_rows:
                    # row_data is like ['ID', 'int64', '0.00%', 'Mean: ...']
                    col_name = row_data[0]
                    original_dtype = row_data[1]
                    # Get short form, default to 'other' if not in map
                    short_dtype = dtype_map.get(original_dtype, 'other') 
                    combined_col_info = f"{col_name}\n({short_dtype})"
                    
                    # Create the new row for the Word table
                    new_row_for_table = [combined_col_info, row_data[2], row_data[3]] # Use original missing % and stats summary

                    row_cells = table.add_row().cells
                    for i, cell_data in enumerate(new_row_for_table):
                        row_cells[i].c.text = str(cell_data)

        elif item_type == "image":
            # Image data is expected as BytesIO object
            try:
                document.add_picture(content, width=Inches(6)) # Adjust width as needed
                last_paragraph = document.paragraphs[-1]
                last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                caption = item.get("caption", "")
                if caption:
                    document.add_paragraph(caption, style='Caption') # Add caption style if available
            except Exception as e:
                document.add_paragraph(f"Error adding image to report: {e}")
        
        elif item_type == "stat_table": # For structured statistical tables
            table_title = item.get("title")
            df_to_add = item.get("dataframe")

            if table_title and df_to_add is not None:
                document.add_paragraph(table_title, style='Heading 4') # Use a sub-heading for the table
                
                # Create a new table in the document
                table = document.add_table(rows=df_to_add.shape[0] + 1, cols=df_to_add.shape[1])
                table.style = 'Table Grid' # Apply a basic table style

                # Add header row
                for i, col_name in enumerate(df_to_add.columns):
                    cell = table.cell(0, i)
                    cell.text = str(col_name)
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.bold = True

                # Add data rows
                for r_idx, row_data in enumerate(df_to_add.itertuples(index=False), start=1):
                    for c_idx, cell_value in enumerate(row_data):
                        # Format floats to 4 decimal places
                        table.cell(r_idx, c_idx).text = str(f"{cell_value:.4f}" if isinstance(cell_value, (float)) else cell_value)
            
        document.add_paragraph("\n") # Add a blank line after each section for spacing

    # Add a section for the footer in the report
    document.add_section(WD_SECTION_START.NEW_PAGE) # Start new page for footer/disclaimer
    footer_paragraph = document.add_paragraph()
    footer_run = footer_paragraph.add_run("SSO Consultants © 2025 | All Rights Reserved.")
    footer_run.font.size = Pt(9)
    footer_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Save document to a BytesIO object
    bio = io.BytesIO()
    document.save(bio)
    bio.seek(0) # Rewind the buffer to the beginning
    return bio

def generate_and_display_graph(df, graph_type, columns):
    """
    Generates a graph (histogram, box plot, scatter plot, correlation heatmap, bar chart)
    and returns its BytesIO buffer and a description.
    """
    img_buffer = io.BytesIO()
    graph_desc = ""
    plt.figure(figsize=(10, 6)) # Set a default figure size

    try:
        if graph_type == "histogram":
            if not columns or not pd.api.types.is_numeric_dtype(df[columns[0]]):
                return None, "Unsupported graph type requested or invalid column for histogram. Please select a numerical column."
            sns.histplot(df[columns[0]].dropna(), kde=True)
            plt.title(f'Histogram of {columns[0]}')
            plt.xlabel(columns[0])
            plt.ylabel('Frequency')
            graph_desc = f"A histogram for the '{columns[0]}' column was generated. It shows the distribution of values for this numerical feature."
        
        elif graph_type == "box_plot":
            if not columns or not pd.api.types.is_numeric_dtype(df[columns[0]]):
                return None, "Unsupported graph type requested or invalid column for box plot. Please select a numerical column."
            sns.boxplot(y=df[columns[0]].dropna())
            plt.title(f'Box Plot of {columns[0]}')
            plt.ylabel(columns[0])
            graph_desc = f"A box plot for the '{columns[0]}' column was generated. It displays the distribution, median, quartiles, and potential outliers of this numerical feature."

        elif graph_type == "scatter_plot":
            if len(columns) != 2 or not pd.api.types.is_numeric_dtype(df[columns[0]]) or not pd.api.types.is_numeric_dtype(df[columns[1]]):
                return None, "Unsupported graph type requested or invalid columns for scatter plot. Please select two numerical columns."
            sns.scatterplot(x=df[columns[0]], y=df[columns[1]])
            plt.title(f'Scatter Plot of {columns[0]} vs {columns[1]}')
            plt.xlabel(columns[0])
            plt.ylabel(columns[1])
            graph_desc = f"A scatter plot of '{columns[0]}' versus '{columns[1]}' was generated. It visualizes the relationship between these two numerical features."

        elif graph_type == "correlation_heatmap":
            numerical_cols = df.select_dtypes(include=['number']).columns
            if numerical_cols.empty:
                return None, "No numerical columns found to generate a correlation heatmap."
            correlation_matrix = df[numerical_cols].corr()
            sns.heatmap(correlation_matrix, annot=True, cmap='coolwarm', fmt=".2f")
            plt.title("Correlation Matrix of Numerical Features")
            graph_desc = "A correlation heatmap of all numerical features was generated. It displays the pairwise correlation coefficients, indicating the strength and direction of linear relationships between variables."

        elif graph_type == "bar_chart":
            if not columns or not (pd.api.types.is_object_dtype(df[columns[0]]) or pd.api.types.is_string_dtype(df[columns[0]]) or pd.api.types.is_categorical_dtype(df[columns[0]])):
                return None, "Unsupported graph type requested or invalid column for bar chart. Please select a categorical column."
            
            # For bar charts, limit to top 10 categories to avoid clutter
            value_counts = df[columns[0]].value_counts().head(10)
            if value_counts.empty:
                return None, f"No data found for bar chart in column '{columns[0]}'."

            sns.barplot(x=value_counts.index, y=value_counts.values)
            plt.title(f'Frequency of {columns[0]} (Top {len(value_counts)})')
            plt.xlabel(columns[0])
            plt.ylabel('Count')
            plt.xticks(rotation=45, ha='right') # Rotate labels for readability
            plt.tight_layout() # Adjust layout to prevent labels overlapping
            graph_desc = f"A bar chart showing the frequency of top categories in '{columns[0]}' was generated. It helps visualize the distribution of categorical values."
        
        else:
            return None, "Unsupported graph type requested. Please ask for a histogram, box plot, scatter plot, correlation heatmap, or bar chart."

        plt.savefig(img_buffer, format='png', bbox_inches='tight') # Save to buffer
        img_buffer.seek(0) # Rewind the buffer
        plt.close() # Close the plot to free memory
        return img_buffer, graph_desc

    except Exception as e:
        plt.close() # Ensure plot is closed even on error
        return None, f"An error occurred while generating the graph: {e}. Please check your column selections and data."


def perform_statistical_test(df, test_type, col1, col2=None):
    """
    Performs the selected statistical test and returns the results as a formatted string
    and optionally a pandas DataFrame(s) for structured output.
    Returns (results_str, structured_results_for_ui, error_message).
    structured_results_for_ui will be None if not applicable or on error, or a DataFrame/tuple of DataFrames.
    """
    results_str = ""
    structured_results_for_ui = None # New: To hold structured results for UI display (e.g., tuple of DFs)
    error_message = None

    try:
        if test_type == "anova":
            append_debug_log(f"DEBUG ANOVA: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG ANOVA: is_numeric_dtype(df[{col1}])={pd.api.types.is_numeric_dtype(df[col1])}")
            append_debug_log(f"DEBUG ANOVA: is_categorical_dtype(df[{col2}])={pd.api.types.is_categorical_dtype(df[col2])} | is_object_dtype(df[{col2}])={pd.api.types.is_object_dtype(df[col2])} | is_string_dtype(df[{col2}])={pd.api.types.is_string_dtype(df[col2])}")
            if not pd.api.types.is_numeric_dtype(df[col1]):
                error_message = f"ANOVA: Dependent variable '{col1}' must be numerical."
            elif not (pd.api.types.is_object_dtype(df[col2]) or pd.api.types.is_string_dtype(df[col2]) or pd.api.types.is_categorical_dtype(df[col2])):
                error_message = f"ANOVA: Independent variable '{col2}' must be categorical."
            else:
                # Drop NaNs from both columns for consistent analysis
                clean_df = df[[col1, col2]].dropna()
                
                groups = [clean_df[col1][clean_df[col2] == g] for g in clean_df[col2].unique()]
                append_debug_log(f"DEBUG ANOVA: unique_groups={clean_df[col2].unique()}, len(groups)={len(groups)}")
                
                if len(groups) < 2:
                    error_message = f"ANOVA: Independent variable '{col2}' needs at least 2 distinct groups."
                elif any(len(g) == 0 for g in groups):
                    error_message = f"ANOVA: Some groups in '{col2}' have no data for '{col1}' after dropping NaNs."
                else:
                    # Perform ANOVA using scipy
                    f_statistic_scipy, p_value_scipy = stats.f_oneway(*groups)

                    # Calculate Sum of Squares (SS) and Degrees of Freedom (df) for ANOVA table
                    grand_mean = clean_df[col1].mean()
                    
                    # Sum of Squares Total (SST)
                    sst = np.sum((clean_df[col1] - grand_mean)**2)
                    df_total = len(clean_df) - 1

                    # Sum of Squares Between (SSB)
                    ssb = 0
                    for g in clean_df[col2].unique():
                        group_data = clean_df[col1][clean_df[col2] == g]
                        ssb += len(group_data) * (group_data.mean() - grand_mean)**2
                    df_between = len(clean_df[col2].unique()) - 1

                    # Sum of Squares Within (SSW)
                    ssw = np.sum((clean_df[col1] - clean_df.groupby(col2)[col1].transform('mean'))**2)
                    df_within = len(clean_df) - len(clean_df[col2].unique())

                    # Mean Squares
                    msb = ssb / df_between if df_between > 0 else np.nan
                    msw = ssw / df_within if df_within > 0 else np.nan

                    # F-statistic (re-calculated to match SS/MS for table consistency, should be close to scipy's)
                    f_stat_calculated = msb / msw if msw > 0 else np.nan

                    # Create ANOVA Summary DataFrame
                    anova_summary_data = {
                        'Source of Variation': ['Between Groups', 'Within Groups', 'Total'],
                        'Sum of Squares (SS)': [ssb, ssw, sst],
                        'df': [df_between, df_within, df_total],
                        'Mean Squares (MS)': [msb, msw, np.nan], # MS for Total is not typically reported
                        'F': [f_stat_calculated, np.nan, np.nan], # F-stat only for Between Groups
                        'P-value': [p_value_scipy, np.nan, np.nan] # P-value only for Between Groups
                    }
                    anova_df = pd.DataFrame(anova_summary_data)
                    
                    results_str = (
                        f"ANOVA Test Results for '{col1}' by '{col2}':\n"
                        f"  F-statistic: {f_statistic_scipy:.4f}\n" # Use scipy's F-stat for the initial text summary
                        f"  P-value: {p_value_scipy:.4f}\n"
                        "Interpretation will be provided by the AI."
                    )
                    structured_results_for_ui = anova_df # Return the single DataFrame

        elif test_type == "independent_t_test":
            append_debug_log(f"DEBUG Independent T-test: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG Independent T-test: df[{col1}].dtype={df[col1].dtype}, df[{col2}].dtype={df[col2].dtype}")
            if not pd.api.types.is_numeric_dtype(df[col1]):
                error_message = f"Independent T-test: Numerical variable '{col1}' must be numerical."
            elif not (pd.api.types.is_object_dtype(df[col2]) or pd.api.types.is_string_dtype(df[col2]) or pd.api.types.is_categorical_dtype(df[col2])):
                error_message = f"Independent T-test: Grouping variable '{col2}' must be categorical."
            else:
                unique_groups = df[col2].unique()
                append_debug_log(f"DEBUG Independent T-test: unique_groups={unique_groups}, len(unique_groups)={len(unique_groups)}")
                if len(unique_groups) != 2:
                    error_message = f"Independent T-test: Grouping variable '{col2}' must have exactly 2 distinct groups. Found {len(unique_groups)}."
                else:
                    group1_name = unique_groups[0]
                    group2_name = unique_groups[1]
                    group1_data = df[col1][df[col2] == group1_name].dropna()
                    group2_data = df[col1][df[col2] == group2_name].dropna()
                    append_debug_log(f"DEBUG Independent T-test: group1_data_len={len(group1_data)}, group2_data_len={len(group2_data)}")
                    if len(group1_data) == 0 or len(group2_data) == 0:
                        error_message = f"Independent T-test: One or both groups have no data for '{col1}' after dropping NaNs."
                    else:
                        t_statistic, p_value = stats.ttest_ind(group1_data, group2_data, equal_var=True) # Assuming equal variances for this specific test
                        
                        # Prepare structured results (Excel-like output)
                        group_stats_data = {
                            'Group': [group1_name, group2_name],
                            'N': [len(group1_data), len(group2_data)],
                            'Mean': [group1_data.mean(), group2_data.mean()],
                            'Std. Deviation': [group1_data.std(), group2_data.std()]
                        }
                        group_stats_df = pd.DataFrame(group_stats_data)

                        test_results_data = {
                            'Statistic': ['T-statistic', 'P-value'],
                            'Value': [t_statistic, p_value]
                        }
                        test_results_df = pd.DataFrame(test_results_data)

                        results_str = (
                            f"Independent T-test (Equal Variances Assumed) Results for '{col1}' by '{col2}' ({group1_name} vs {group2_name}):\n"
                            f"  T-statistic: {t_statistic:.4f}\n"
                            f"  P-value: {p_value:.4f}\n"
                            "Interpretation will be provided by the AI."
                        )
                        # Store both dataframes in a tuple for structured_results_for_ui
                        structured_results_for_ui = (group_stats_df, test_results_df)

        elif test_type == "chi_squared_test":
            append_debug_log(f"DEBUG Chi-squared: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG Chi-squared: df[{col1}].dtype={df[col1].dtype}, df[{col2}].dtype={df[col2].dtype}")
            if not (pd.api.types.is_object_dtype(df[col1]) or pd.api.types.is_string_dtype(df[col1]) or pd.api.types.is_categorical_dtype(df[col1])):
                error_message = f"Chi-squared: Column 1 '{col1}' must be categorical."
            elif not (pd.api.types.is_object_dtype(df[col2]) or pd.api.types.is_string_dtype(df[col2]) or pd.api.types.is_categorical_dtype(df[col2])):
                error_message = f"Chi-squared: Column 2 '{col2}' must be categorical."
            else:
                contingency_table = pd.crosstab(df[col1], df[col2])
                append_debug_log(f"DEBUG Chi-squared: contingency_table_shape={contingency_table.shape}, sum={contingency_table.sum().sum()}")
                if contingency_table.empty or contingency_table.sum().sum() == 0:
                     error_message = f"Chi-squared: No valid data to form a contingency table for '{col1}' and '{col2}'."
                else:
                    chi2, p_value, dof, expected = stats.chi2_contingency(contingency_table)
                    
                    # Create DataFrames for Observed and Expected tables
                    observed_df = contingency_table
                    expected_df = pd.DataFrame(expected, index=contingency_table.index, columns=contingency_table.columns)

                    results_str = (
                        f"Chi-squared Test Results for '{col1}' and '{col2}':\n"
                        f"  Chi-squared statistic: {chi2:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        f"  Degrees of Freedom (dof): {dof}\n"
                        "Interpretation will be provided by the AI."
                    )
                    structured_results_for_ui = (observed_df, expected_df, chi2, p_value, dof) # Return tuple of DFs and key stats

        elif test_type == "paired_t_test":
            append_debug_log(f"DEBUG Paired T-test: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG Paired T-test: df[{col1}].dtype={df[col1].dtype}, df[{col2}].dtype={df[col2].dtype}")
            if not pd.api.types.is_numeric_dtype(df[col1]) or not pd.api.types.is_numeric_dtype(df[col2]):
                error_message = f"Paired T-test: Both '{col1}' and '{col2}' must be numerical variables."
            else:
                # Drop rows where either of the two columns has NaN to ensure paired data
                paired_df = df[[col1, col2]].dropna()
                if len(paired_df) < 2: # Need at least 2 pairs to run t-test
                    error_message = f"Paired T-test: Not enough paired data points after dropping NaNs. Need at least 2."
                else:
                    t_statistic, p_value = stats.ttest_rel(paired_df[col1], paired_df[col2])

                    # Calculate means and std devs for display
                    mean1 = paired_df[col1].mean()
                    std1 = paired_df[col1].std()
                    mean2 = paired_df[col2].mean()
                    std2 = paired_df[col2].std()
                    n_pairs = len(paired_df)

                    # Create Group Statistics table for paired data
                    group_stats_data = {
                        'Variable': [col1, col2],
                        'N': [n_pairs, n_pairs],
                        'Mean': [mean1, mean2],
                        'Std. Deviation': [std1, std2]
                    }
                    group_stats_df = pd.DataFrame(group_stats_data)

                    # Create Test Results table
                    test_results_data = {
                        'Statistic': ['T-statistic', 'P-value'],
                        'Value': [t_statistic, p_value]
                    }
                    test_results_df = pd.DataFrame(test_results_data)

                    results_str = (
                        f"Paired T-test Results for '{col1}' and '{col2}':\n"
                        f"  T-statistic: {t_statistic:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        "Interpretation will be provided by the AI."
                    )
                    structured_results_for_ui = (group_stats_df, test_results_df)

        elif test_type == "pearson_correlation":
            append_debug_log(f"DEBUG Pearson Correlation: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG Pearson Correlation: df[{col1}].dtype={df[col1].dtype}, df[{col2}].dtype={df[col2].dtype}")
            if not pd.api.types.is_numeric_dtype(df[col1]) or not pd.api.types.is_numeric_dtype(df[col2]):
                error_message = f"Pearson Correlation: Both '{col1}' and '{col2}' must be numerical variables."
            else:
                # Drop rows where either of the two columns has NaN to ensure consistent length
                clean_df = df[[col1, col2]].dropna()
                if len(clean_df) < 2: # Need at least 2 data points for correlation
                    error_message = f"Pearson Correlation: Not enough data points after dropping NaNs. Need at least 2."
                else:
                    correlation_coefficient, p_value = stats.pearsonr(clean_df[col1], clean_df[col2])
                    covariance = clean_df[col1].cov(clean_df[col2]) # Calculate covariance
                    n_obs = len(clean_df)

                    # Create Correlation Results table
                    corr_results_data = {
                        'Statistic': ['Pearson Correlation (r)', 'Covariance', 'P-value', 'N'],
                        'Value': [correlation_coefficient, covariance, p_value, n_obs]
                    }
                    corr_results_df = pd.DataFrame(corr_results_data)

                    results_str = (
                        f"Pearson Correlation Results for '{col1}' and '{col2}':\n"
                        f"  Correlation Coefficient (r): {correlation_coefficient:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        f"  Covariance: {covariance:.4f}\n"
                        "Interpretation will be provided by the AI."
                    )
                    structured_results_for_ui = corr_results_df

        elif test_type == "spearman_rank_correlation":
            append_debug_log(f"DEBUG Spearman Correlation: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG Spearman Correlation: df[{col1}].dtype={df[col1].dtype}, df[{col2}].dtype={df[col2].dtype}")
            if not pd.api.types.is_numeric_dtype(df[col1]) or not pd.api.types.is_numeric_dtype(df[col2]): # Spearman can also use ordinal, but for simplicity, we'll stick to numerical selection
                error_message = f"Spearman Rank Correlation: Both '{col1}' and '{col2}' must be numerical variables."
            else:
                # Drop rows where either of the two columns has NaN to ensure consistent length
                clean_df = df[[col1, col2]].dropna()
                if len(clean_df) < 2: # Need at least 2 data points for correlation
                    error_message = f"Spearman Rank Correlation: Not enough data points after dropping NaNs. Need at least 2."
                else:
                    correlation_coefficient, p_value = stats.spearmanr(clean_df[col1], clean_df[col2])
                    n_obs = len(clean_df)

                    # Create Correlation Results table
                    spearman_results_data = {
                        'Statistic': ['Spearman Correlation (rho)', 'P-value', 'N'],
                        'Value': [correlation_coefficient, p_value, n_obs]
                    }
                    spearman_results_df = pd.DataFrame(spearman_results_data)

                    results_str = (
                        f"Spearman Rank Correlation Results for '{col1}' and '{col2}':\n"
                        f"  Correlation Coefficient (rho): {correlation_coefficient:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        "Interpretation will be provided by the AI."
                    )
                    structured_results_for_ui = spearman_results_df

        elif test_type == "f_test_two_sample_for_variances":
            append_debug_log(f"DEBUG F-Test for Variances: col1={col1}, col2={col2}")
            append_debug_log(f"DEBUG F-Test for Variances: df[{col1}].dtype={df[col1].dtype}, df[{col2}].dtype={df[col2].dtype}")
            if not pd.api.types.is_numeric_dtype(df[col1]) or not pd.api.types.is_numeric_dtype(df[col2]):
                error_message = f"F-Test for Variances: Both '{col1}' and '{col2}' must be numerical variables."
            else:
                data1 = df[col1].dropna()
                data2 = df[col2].dropna()
                if len(data1) < 2 or len(data2) < 2: # Need at least 2 data points for variance calculation
                    error_message = f"F-Test for Variances: Not enough data points for '{col1}' or '{col2}' after dropping NaNs. Need at least 2 for each."
                else:
                    var1 = np.var(data1, ddof=1) # Sample variance
                    var2 = np.var(data2, ddof=1) # Sample variance

                    if var1 == 0 and var2 == 0:
                        f_statistic = np.nan
                        p_value = np.nan
                        warning_message = "Both variances are zero; F-statistic and P-value are undefined."
                    elif var2 == 0: # Avoid division by zero
                        f_statistic = np.inf
                        p_value = 0.0 # Extreme p-value
                        warning_message = f"Variance of '{col2}' is zero. F-statistic is infinite."
                    elif var1 == 0: # Avoid division by zero
                        f_statistic = 0.0
                        p_value = 0.0 # Extreme p-value
                        warning_message = f"Variance of '{col1}' is zero. F-statistic is zero."
                    else:
                        f_statistic = var1 / var2
                        # The p-value for a two-tailed F-test
                        p_value = 2 * min(stats.f.cdf(f_statistic, len(data1) - 1, len(data2) - 1),
                                        1 - stats.f.cdf(f_statistic, len(data1) - 1, len(data2) - 1))
                        warning_message = None

                    # Create Variances and F-Test Results tables
                    variance_stats_data = {
                        'Variable': [col1, col2],
                        'N': [len(data1), len(data2)],
                        'Variance': [var1, var2],
                        'Std. Deviation': [np.std(data1, ddof=1), np.std(data2, ddof=1)]
                    }
                    variance_stats_df = pd.DataFrame(variance_stats_data)

                    f_test_results_data = {
                        'Statistic': ['F-statistic', 'P-value', 'df1', 'df2'],
                        'Value': [f_statistic, p_value, len(data1) - 1, len(data2) - 1]
                    }
                    f_test_results_df = pd.DataFrame(f_test_results_data)

                    results_str = (
                        f"Two-Sample F-Test for Variances Results for '{col1}' and '{col2}':\n"
                        f"  F-statistic: {f_statistic:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        f"  Degrees of Freedom ({col1}): {len(data1) - 1}\n"
                        f"  Degrees of Freedom ({col2}): {len(data2) - 1}\n"
                        "Interpretation will be provided by the AI."
                    )
                    if warning_message:
                        results_str += f"\n  Warning: {warning_message}"
                    structured_results_for_ui = (variance_stats_df, f_test_results_df)

        elif test_type == "shapiro_test": # NEW SHAPIRO TEST LOGIC
            append_debug_log(f"DEBUG Shapiro Test: col1={col1}")
            if not pd.api.types.is_numeric_dtype(df[col1]):
                error_message = f"Shapiro-Wilk Test: Column '{col1}' must be numerical."
            else:
                data = df[col1].dropna()
                if len(data) < 3: # Shapiro-Wilk test requires at least 3 data points
                    error_message = f"Shapiro-Wilk Test: Not enough data points in '{col1}' after dropping NaNs. Need at least 3."
                else:
                    shapiro_statistic, p_value = stats.shapiro(data)
                    
                    shapiro_results_data = {
                        'Statistic': ['Shapiro-Wilk Statistic', 'P-value', 'N'],
                        'Value': [shapiro_statistic, p_value, len(data)]
                    }
                    shapiro_results_df = pd.DataFrame(shapiro_results_data)

                    results_str = (
                        f"Shapiro-Wilk Normality Test Results for '{col1}':\n"
                        f"  Shapiro-Wilk Statistic: {shapiro_statistic:.4f}\n"
                        f"  P-value: {p_value:.4f}\n"
                        "Interpretation will be provided by the AI."
                    )
                    structured_results_for_ui = shapiro_results_df
                    
        else:
            error_message = "Unsupported statistical test type."

    except Exception as e:
        error_message = f"An error occurred during statistical test: {e}. Please check your column selections and data."
        append_debug_log(f"DEBUG: Error in perform_statistical_test: {e}") # Debug print
        st.exception(e) # Display the full traceback in the Streamlit UI for debugging
    
    return results_str, structured_results_for_ui, error_message


# --- Main Application Logic ---

if check_password():
    st.sidebar.title(f"Welcome, {st.session_state['current_username']}!")
    
    # Add a logout button to the sidebar
    if st.sidebar.button("Logout"):
        st.session_state['logged_in'] = False
        st.session_state['current_username'] = None
        st.session_state['df'] = None # Clear data on logout
        st.session_state['data_summary_text'] = ""
        st.session_state['data_summary_table'] = []
        st.session_state['messages'] = []
        st.session_state['report_content'] = []
        st.session_state['user_goal'] = "Not specified"
        st.session_state['uploaded_file_name'] = None
        st.session_state['openai_client_initialized'] = False
        st.session_state['openai_client'] = None
        st.session_state['debug_logs'] = [] # Clear debug logs on logout
        st.rerun()

    # Place AI key check here, after successful login
    if not st.session_state['openai_client_initialized']:
        st.info("Checking OpenAI API key. This may take a moment...")
        if not check_openai_api_key():
            st.warning("AI features are disabled due to an invalid or unreachable OpenAI API key. Please configure it in .streamlit/secrets.toml.")
    else:
        st.sidebar.success("AI features enabled.")

    st.title("Data Preprocessing Assistant")
    st.markdown("---")

    # File Uploader
    st.sidebar.header("Upload Dataset")
    uploaded_file = st.sidebar.file_uploader("Upload CSV, Excel, or JSON", type=["csv", "xlsx", "xls", "json"])

    # Check if a new file has been uploaded or if the session state df is empty
    if uploaded_file is not None and (st.session_state['uploaded_file_name'] != uploaded_file.name or st.session_state['df'] is None):
        st.session_state['uploaded_file_name'] = uploaded_file.name
        
        # Reset session state variables related to the dataset and chat history
        st.session_state['df'] = None
        st.session_state['data_summary_text'] = ""
        st.session_state['data_summary_table'] = []
        st.session_state['messages'] = [] # Clear chat messages for new dataset
        st.session_state['report_content'] = [] # Clear report content for new dataset
        st.session_state['user_goal'] = "Not specified" # Reset user goal
        st.session_state['debug_logs'] = [] # Clear debug logs on new upload
        
        append_debug_log(f"DEBUG: New file uploaded: {uploaded_file.name}") # Debug print

        with st.spinner("Loading and analyzing data..."):
            try:
                if uploaded_file.name.endswith('.csv'):
                    st.session_state['df'] = pd.read_csv(uploaded_file)
                elif uploaded_file.name.endswith(('.xlsx', '.xls')):
                    st.session_state['df'] = pd.read_excel(uploaded_file)
                elif uploaded_file.name.endswith('.json'):
                    st.session_state['df'] = pd.read_json(uploaded_file)
                
                st.sidebar.success(f"Successfully loaded {uploaded_file.name}")
                st.write("### Raw Data Preview")
                st.dataframe(st.session_state['df'].head())
                
                # Generate and store summary for AI and report
                summary_text, summary_table = get_data_summary(st.session_state['df'])
                st.session_state['data_summary_text'] = summary_text
                st.session_state['data_summary_table'] = summary_table

                # Add initial data summary to report content
                st.session_state['report_content'].append({"type": "heading", "content": "1. Dataset Overview", "level": 2})
                st.session_state['report_content'].append({"type": "text", "content": summary_text})
                st.session_state['report_content'].append({"type": "table", "headers": summary_table[0], "rows": summary_table[1:]})

                # Add an initial message to the chat history
                initial_response = (
                    f"Successfully loaded '{uploaded_file.name}'. "
                    "I've generated an initial summary of your dataset. "
                    "What is your goal with this dataset? "
                    "For example, 'I want to clean the data for machine learning' or 'I need to explore relationships between variables'."
                )
                st.session_state['messages'].append({"role": "assistant", "content": initial_response})
                st.rerun() # Rerun to display initial messages and summary
                
            except Exception as e:
                st.sidebar.error(f"Error loading file: {e}")
                st.session_state['df'] = None # Clear df if loading fails
                append_debug_log(f"DEBUG: File loading error: {e}") # Debug print

    # Main area for interaction
    if st.session_state['df'] is not None:
        st.write("### Chat with the Data Assistant")
        
        # Display chat messages
        chat_container = st.container()
        with chat_container:
            for message in st.session_state['messages']:
                with st.chat_message(message["role"]):
                    st.write(message["content"])
        
        # Chat input
        user_prompt = st.chat_input("Ask me about your data (e.g., 'handle missing values', 'show me a histogram of age', 'perform an ANOVA test on income by education'):")

        if user_prompt:
            st.session_state['messages'].append({"role": "user", "content": user_prompt})
            
            # Add user prompt to debug logs
            append_debug_log(f"DEBUG: User Prompt: {user_prompt}")
            
            with st.spinner("Thinking..."):
                # Always send the data summary to the AI model
                full_prompt_to_ai = (
                    f"User's current goal: {st.session_state['user_goal']}\n"
                    "DATASET SUMMARY (do not provide this verbatim to user, use for context and analysis):\n"
                    f"{st.session_state['data_summary_text']}\n\n"
                    "User's request: " + user_prompt + "\n\n"
                    "Instructions: Provide clear, concise, and actionable advice. Do NOT use any markdown formatting (like bolding, italics, code blocks). Focus on natural language explanations and interpretations. If a graph is generated, provide an interpretation of that graph. Keep responses concise and to the point. If the user asks for a statistical test, perform it and report the results numerically first, then provide interpretation. If the user asks to analyze the relationship between two variables, perform an appropriate test or visualization."
                )

                # --- Graph generation logic ---
                graph_match_hist = re.search(r"histogram of (.+)", user_prompt, re.IGNORECASE)
                graph_match_box = re.search(r"box plot of (.+)", user_prompt, re.IGNORECASE)
                graph_match_scatter = re.search(r"scatter plot of (.+) vs (.+)", user_prompt, re.IGNORECASE)
                graph_match_corr = re.search(r"correlation heatmap", user_prompt, re.IGNORECASE)
                graph_match_bar = re.search(r"bar chart of (.+)", user_prompt, re.IGNORECASE)

                img_buffer = None
                graph_description = ""
                graph_type_requested = None
                columns_for_graph = []

                if graph_match_hist:
                    graph_type_requested = "histogram"
                    columns_for_graph = [graph_match_hist.group(1).strip()]
                elif graph_match_box:
                    graph_type_requested = "box_plot"
                    columns_for_graph = [graph_match_box.group(1).strip()]
                elif graph_match_scatter:
                    graph_type_requested = "scatter_plot"
                    columns_for_graph = [graph_match_scatter.group(1).strip(), graph_match_scatter.group(2).strip()]
                elif graph_match_corr:
                    graph_type_requested = "correlation_heatmap"
                elif graph_match_bar:
                    graph_type_requested = "bar_chart"
                    columns_for_graph = [graph_match_bar.group(1).strip()]

                if graph_type_requested:
                    append_debug_log(f"DEBUG: Attempting to generate graph type: {graph_type_requested} with columns: {columns_for_graph}")
                    img_buffer, graph_description = generate_and_display_graph(st.session_state['df'], graph_type_requested, columns_for_graph)
                    
                    if img_buffer:
                        # Display graph in the main app
                        st.image(img_buffer, caption=graph_description, use_column_width=True)
                        st.session_state['messages'].append({"role": "assistant", "content": f"Here is the requested graph: {graph_description}"})
                        # Add graph to report content
                        img_buffer.seek(0) # Rewind for report
                        st.session_state['report_content'].append({"type": "image", "content": img_buffer.getvalue(), "caption": graph_description})
                        
                        # Get AI interpretation for the graph
                        interpretation_prompt = (
                            f"Based on the following graph description and the dataset summary, provide a concise interpretation. "
                            f"Graph: {graph_description}. Dataset Summary:\n{st.session_state['data_summary_text']}\n\n"
                            f"Provide only the interpretation, no other text or formatting."
                        )
                        ai_interpretation = generate_openai_response(interpretation_prompt)
                        if ai_interpretation:
                            st.write(f"**AI Interpretation:** {ai_interpretation}")
                            st.session_state['messages'].append({"role": "assistant", "content": f"AI Interpretation: {ai_interpretation}"})
                            st.session_state['report_content'].append({"type": "text", "content": f"**AI Interpretation:** {ai_interpretation}"})

                    else:
                        st.error(graph_description) # Display error message from graph function
                        st.session_state['messages'].append({"role": "assistant", "content": f"I couldn't generate the graph: {graph_description}"})
                
                # --- Statistical Test Logic ---
                stat_test_requested = None
                col1_stat = None
                col2_stat = None
                
                # Pattern for ANOVA (numerical by categorical)
                anova_match = re.search(r"anova test on (.+) by (.+)", user_prompt, re.IGNORECASE)
                # Pattern for Independent T-test (numerical by binary categorical)
                t_test_ind_match = re.search(r"independent t-test on (.+) by (.+)", user_prompt, re.IGNORECASE)
                # Pattern for Chi-squared test (categorical by categorical)
                chi_squared_match = re.search(r"chi-squared test on (.+) and (.+)", user_prompt, re.IGNORECASE)
                # Pattern for Paired T-test (numerical before/after)
                paired_t_test_match = re.search(r"paired t-test on (.+) and (.+)", user_prompt, re.IGNORECASE)
                # Pattern for Pearson Correlation (numerical vs numerical)
                pearson_corr_match = re.search(r"(?:pearson|correlation) (?:coefficient|test) on (.+) and (.+)", user_prompt, re.IGNORECASE)
                # Pattern for Spearman Rank Correlation (numerical/ordinal vs numerical/ordinal)
                spearman_corr_match = re.search(r"spearman(?: rank)? correlation on (.+) and (.+)", user_prompt, re.IGNORECASE)
                # Pattern for F-test for variances (numerical vs numerical for variances)
                f_test_var_match = re.search(r"f-test for variances on (.+) and (.+)", user_prompt, re.IGNORECASE)
                # NEW: Pattern for Shapiro-Wilk Test
                shapiro_match = re.search(r"(?:shapiro|shapiro-wilk) test on (.+)", user_prompt, re.IGNORECASE)


                if anova_match:
                    stat_test_requested = "anova"
                    col1_stat = anova_match.group(1).strip() # Dependent (numerical)
                    col2_stat = anova_match.group(2).strip() # Independent (categorical)
                elif t_test_ind_match:
                    stat_test_requested = "independent_t_test"
                    col1_stat = t_test_ind_match.group(1).strip() # Numerical variable
                    col2_stat = t_test_ind_match.group(2).strip() # Grouping variable (binary categorical)
                elif chi_squared_match:
                    stat_test_requested = "chi_squared_test"
                    col1_stat = chi_squared_match.group(1).strip() # Categorical 1
                    col2_stat = chi_squared_match.group(2).strip() # Categorical 2
                elif paired_t_test_match:
                    stat_test_requested = "paired_t_test"
                    col1_stat = paired_t_test_match.group(1).strip() # Numerical 1 (e.g., before)
                    col2_stat = paired_t_test_match.group(2).strip() # Numerical 2 (e.g., after)
                elif pearson_corr_match:
                    stat_test_requested = "pearson_correlation"
                    col1_stat = pearson_corr_match.group(1).strip()
                    col2_stat = pearson_corr_match.group(2).strip()
                elif spearman_corr_match:
                    stat_test_requested = "spearman_rank_correlation"
                    col1_stat = spearman_corr_match.group(1).strip()
                    col2_stat = spearman_corr_match.group(2).strip()
                elif f_test_var_match:
                    stat_test_requested = "f_test_two_sample_for_variances"
                    col1_stat = f_test_var_match.group(1).strip()
                    col2_stat = f_test_var_match.group(2).strip()
                elif shapiro_match: # NEW SHAPIRO MATCH
                    stat_test_requested = "shapiro_test"
                    col1_stat = shapiro_match.group(1).strip()
                    col2_stat = None # Shapiro test only takes one column


                if stat_test_requested:
                    append_debug_log(f"DEBUG: Attempting to perform statistical test: {stat_test_requested} with columns: {col1_stat}, {col2_stat}")
                    
                    # Check if columns exist in DataFrame (only col1_stat needs to be checked for Shapiro)
                    if col1_stat not in st.session_state['df'].columns:
                        st.error(f"Column '{col1_stat}' not found in the dataset.")
                        st.session_state['messages'].append({"role": "assistant", "content": f"I cannot find the column '{col1_stat}' in your dataset. Please check the column name."})
                        st.rerun()
                        return
                    if col2_stat and col2_stat not in st.session_state['df'].columns:
                        st.error(f"Column '{col2_stat}' not found in the dataset.")
                        st.session_state['messages'].append({"role": "assistant", "content": f"I cannot find the column '{col2_stat}' in your dataset. Please check the column name."})
                        st.rerun()
                        return

                    test_results_str, structured_results, test_error = perform_statistical_test(
                        st.session_state['df'], stat_test_requested, col1_stat, col2_stat
                    )

                    if test_error:
                        st.error(test_error)
                        st.session_state['messages'].append({"role": "assistant", "content": f"I couldn't perform the statistical test: {test_error}"})
                    else:
                        st.write("### Statistical Test Results")
                        st.write(test_results_str)
                        st.session_state['messages'].append({"role": "assistant", "content": test_results_str})
                        
                        # Display structured results in UI and add to report
                        if structured_results is not None:
                            if isinstance(structured_results, pd.DataFrame):
                                st.dataframe(structured_results)
                                st.session_state['report_content'].append({
                                    "type": "stat_table",
                                    "title": f"Results for {stat_test_requested.replace('_', ' ').title()}",
                                    "dataframe": structured_results
                                })
                            elif isinstance(structured_results, tuple):
                                # Handle tuples of DataFrames (e.g., T-test, Chi-squared)
                                if stat_test_requested == "independent_t_test":
                                    group_stats_df, test_stats_df = structured_results
                                    st.write("#### Group Statistics")
                                    st.dataframe(group_stats_df)
                                    st.session_state['report_content'].append({
                                        "type": "stat_table",
                                        "title": "Group Statistics",
                                        "dataframe": group_stats_df
                                    })
                                    st.write("#### Test Results")
                                    st.dataframe(test_stats_df)
                                    st.session_state['report_content'].append({
                                        "type": "stat_table",
                                        "title": "Test Results",
                                        "dataframe": test_stats_df
                                    })
                                elif stat_test_requested == "chi_squared_test":
                                    observed_df, expected_df, chi2_val, p_val, dof_val = structured_results
                                    st.write("#### Observed Frequencies")
                                    st.dataframe(observed_df)
                                    st.session_state['report_content'].append({
                                        "type": "stat_table",
                                        "title": "Observed Frequencies",
                                        "dataframe": observed_df
                                    })
                                    st.write("#### Expected Frequencies")
                                    st.dataframe(expected_df)
                                    st.session_state['report_content'].append({
                                        "type": "stat_table",
                                        "title": "Expected Frequencies",
                                        "dataframe": expected_df
                                    })
                                    st.write(f"Chi-squared = {chi2_val:.4f}, p-value = {p_val:.4f}, df = {dof_val}")
                                    st.session_state['report_content'].append({
                                        "type": "text",
                                        "content": f"Chi-squared = {chi2_val:.4f}, p-value = {p_val:.4f}, df = {dof_val}"
                                    })
                                elif stat_test_requested == "paired_t_test":
                                    group_stats_df, test_stats_df = structured_results
                                    st.write("#### Paired Sample Statistics")
                                    st.dataframe(group_stats_df)
                                    st.session_state['report_content'].append({
                                        "type": "stat_table",
                                        "title": "Paired Sample Statistics",
                                        "dataframe": group_stats_df
                                    })
                                    st.write("#### Paired Sample Test Results")
                                    st.dataframe(test_stats_df)
                                    st.session_state['report_content'].append({
                                        "type": "stat_table",
                                        "title": "Paired Sample Test Results",
                                        "dataframe": test_stats_df
                                    })
                                elif stat_test_requested == "f_test_two_sample_for_variances":
                                    variance_stats_df, f_test_results_df = structured_results
                                    st.write("#### Variance Statistics")
                                    st.dataframe(variance_stats_df)
                                    st.session_state['report_content'].append({
                                        "type": "stat_table",
                                        "title": "Variance Statistics",
                                        "dataframe": variance_stats_df
                                    })
                                    st.write("#### F-Test for Variances Results")
                                    st.dataframe(f_test_results_df)
                                    st.session_state['report_content'].append({
                                        "type": "stat_table",
                                        "title": "F-Test for Variances Results",
                                        "dataframe": f_test_results_df
                                    })


                        # Get AI interpretation for the statistical test
                        interpretation_prompt = (
                            f"Based on the following statistical test results and the dataset summary, provide a concise interpretation of the findings. "
                            f"Test Results:\n{test_results_str}\n\n"
                            f"Dataset Summary:\n{st.session_state['data_summary_text']}\n\n"
                            f"Provide only the interpretation, no other text or formatting."
                        )
                        ai_interpretation = generate_openai_response(interpretation_prompt)
                        if ai_interpretation:
                            st.write(f"**AI Interpretation:** {ai_interpretation}")
                            st.session_state['messages'].append({"role": "assistant", "content": f"AI Interpretation: {ai_interpretation}"})
                            st.session_state['report_content'].append({"type": "text", "content": f"**AI Interpretation:** {ai_interpretation}"})
                    
                    st.rerun() # Rerun to display test results

                # --- Handle user goal update ---
                goal_match = re.search(r"(my goal is|i want to|i need to) (.+)", user_prompt, re.IGNORECASE)
                if goal_match:
                    st.session_state['user_goal'] = goal_match.group(2).strip()
                    response_content = f"Understood! Your goal is: {st.session_state['user_goal']}. How can I help you achieve this goal with your dataset?"
                    st.session_state['messages'].append({"role": "assistant", "content": response_content})
                    st.rerun()
                elif not graph_type_requested and not stat_test_requested: # If not a graph or stat test, send to general AI
                    ai_response = generate_openai_response(full_prompt_to_ai)
                    st.session_state['messages'].append({"role": "assistant", "content": ai_response})
                    st.session_state['report_content'].append({"type": "text", "content": ai_response})
                    st.rerun()


    # Download Report Button
    if st.sidebar.button("Generate & Download Report"):
        if st.session_state['df'] is not None:
            with st.spinner("Generating report..."):
                report_buffer = create_report_doc(st.session_state['report_content'])
                st.sidebar.download_button(
                    label="Download Report (.docx)",
                    data=report_buffer,
                    file_name="Data_Preprocessing_Report.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="download_report_button"
                )
        else:
            st.sidebar.warning("Please upload a dataset first to generate a report.")

    # --- Footer ---
    st.markdown("---")
    # Centering the footer
    st.markdown(
        "<div style='text-align: center;'>"\
        "SSO Consultants © 2025 | All Rights Reserved."\
        "</div>",
        unsafe_allow_html=True
    )

    # --- In-App Debug Logs ---
    st.expander_debug = st.expander("Show Debug Logs")
    with st.expander_debug:
        if st.button("Clear Debug Logs", key="clear_debug_logs_button"):
            st.session_state['debug_logs'] = []
            st.rerun()
        for log_entry in st.session_state['debug_logs']:
            st.code(log_entry, language='text')

# --- Run the App ---
if not st.session_state['logged_in']:
    check_password()
