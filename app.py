import streamlit as st
import pandas as pd
import io
import os
from openai import OpenAI
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION_START
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import matplotlib.pyplot as plt
import seaborn as sns
import re # For parsing graph requests

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
# "ssoconsultants14@gmail.com" = "Sso@123"

# Load secrets
try:
    OPENAI_API_KEY = st.secrets["openai"]["api_key"] # Accessing the api_key from the [openai] section
    LOGIN_USERS = st.secrets["credentials"]          # Accessing the users from the [credentials] section
except KeyError as e:
    st.error(f"Secret not found: {e}. Please ensure your .streamlit/secrets.toml file is correctly configured with [openai] and [credentials] sections.")
    st.stop() # Stop the app if secrets are missing

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

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

# --- Helper Functions ---

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

    summary_text.append("Column Details:\n")
    for col in df.columns:
        dtype = df[col].dtype
        missing_percent = df[col].isnull().sum() / len(df) * 100
        
        col_summary_text = []
        col_table_summary = ""

        col_summary_text.append(f"  - Column '{col}':")
        col_summary_text.append(f"    - Data Type: {dtype}")
        col_summary_text.append(f"    - Missing Values: {missing_percent:.2f}%")

        if pd.api.types.is_numeric_dtype(df[col]):
            desc = df[col].describe()
            col_summary_text.append(f"    - Numerical Stats:")
            col_summary_text.append(f"      - Mean: {desc['mean']:.2f}")
            col_summary_text.append(f"      - Median: {df[col].median():.2f}")
            col_summary_text.append(f"      - Std Dev: {desc['std']:.2f}")
            col_summary_text.append(f"      - Min: {desc['min']:.2f}")
            col_summary_text.append(f"      - Max: {desc['max']:.2f}")
            col_summary_text.append(f"      - 25th Percentile: {desc['25%']:.2f}")
            col_summary_text.append(f"      - 75th Percentile: {desc['75%']:.2f}")
            col_summary_text.append(f"      - Skewness: {df[col].skew():.2f}")
            col_summary_text.append(f"      - Kurtosis: {df[col].kurt():.2f}")
            col_table_summary = f"Mean: {desc['mean']:.2f}, Median: {df[col].median():.2f}, Missing: {missing_percent:.2f}%"
        elif pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
            unique_count = df[col].nunique()
            top_values = df[col].value_counts().head(3) # Limit to top 3 for table summary
            col_summary_text.append(f"    - Categorical Stats:")
            col_summary_text.append(f"      - Unique Values (Cardinality): {unique_count}")
            if not top_values.empty:
                col_summary_text.append(f"      - Top 3 Values and Counts:")
                top_vals_str = ", ".join([f"'{val}': {count}" for val, count in top_values.items()])
                col_summary_text.append(f"        - {top_vals_str}")
                col_table_summary = f"Unique: {unique_count}, Top: {top_vals_str}, Missing: {missing_percent:.2f}%"
            else:
                col_table_summary = f"Unique: {unique_count}, Missing: {missing_percent:.2f}%"
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            col_summary_text.append(f"    - Date/Time Stats:")
            col_summary_text.append(f"      - Min Date: {df[col].min()}")
            col_summary_text.append(f"      - Max Date: {df[col].max()}")
            col_table_summary = f"Min Date: {df[col].min().strftime('%Y-%m-%d')}, Max Date: {df[col].max().strftime('%Y-%m-%d')}, Missing: {missing_percent:.2f}%"
        
        summary_text.append("\n".join(col_summary_text))
        summary_text.append("") # Add a blank line for readability between columns in text summary
        column_details_for_table.append([col, str(dtype), f"{missing_percent:.2f}%", col_table_summary])

    return "\n".join(summary_text), column_details_for_table

def generate_openai_response(prompt, model="gpt-3.5-turbo"):
    """
    Sends a prompt to the OpenAI API and returns the response.
    Explicitly instructs the model NOT to provide Python code snippets.
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a data preprocessing expert. Provide clear, concise, and actionable advice. Do NOT provide Python code snippets in your responses. Focus on natural language explanations and interpretations. Always ask the user about their goal for the dataset if not specified, or if a graph is generated, provide an interpretation of that graph."},
                *st.session_state.messages, # Include full conversation history
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1000,
            top_p=1.0,
            frequency_penalty=0.0,
            presence_penalty=0.0
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"Error communicating with OpenAI API: {e}")
        return "I'm sorry, I'm having trouble connecting to the AI. Please try again later."

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
            document.add_heading(content, level=item.get("level", 2))
        elif item_type == "text":
            document.add_paragraph(content)
        elif item_type == "table":
            headers = item.get("headers", [])
            rows = item.get("rows", [])
            
            if headers and rows:
                document.add_heading("Column Details", level=3) # Specific heading for the table
                table = document.add_table(rows=1, cols=len(headers))
                table.style = 'Table Grid' # Apply a basic table style

                # Add headers
                hdr_cells = table.rows[0].cells
                for i, header_text in enumerate(headers):
                    hdr_cells[i].text = header_text
                    # Set header bold
                    for paragraph in hdr_cells[i].paragraphs:
                        for run in paragraph.runs:
                            run.bold = True

                # Add rows
                for row_data in rows:
                    row_cells = table.add_row().cells
                    for i, cell_data in enumerate(row_data):
                        row_cells[i].text = str(cell_data)
        elif item_type == "image":
            # Image data is expected as BytesIO object
            try:
                document.add_picture(content, width=Inches(6)) # Adjust width as needed
                last_paragraph = document.paragraphs[-1]
                last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                document.add_paragraph(item.get("caption", "")) # Add caption if available
            except Exception as e:
                document.add_paragraph(f"Error adding image to report: {e}")
        
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
    Generates and displays a graph based on user request.
    Returns a description of the generated graph for AI interpretation.
    """
    fig, ax = plt.subplots(figsize=(10, 6))
    graph_description = ""
    
    try:
        if graph_type == "histogram":
            if len(columns) == 1 and pd.api.types.is_numeric_dtype(df[columns[0]]):
                sns.histplot(df[columns[0]], kde=True, ax=ax)
                ax.set_title(f"Histogram of {columns[0]}")
                ax.set_xlabel(columns[0])
                ax.set_ylabel("Frequency")
                graph_description = f"A histogram for the '{columns[0]}' column was generated. It shows the distribution of values for this numerical feature."
            else:
                st.warning("Please specify a single numerical column for a histogram.")
                return None, "The requested histogram could not be generated. Please ensure you specify one numerical column."

        elif graph_type == "boxplot":
            if len(columns) == 1 and pd.api.types.is_numeric_dtype(df[columns[0]]):
                sns.boxplot(y=df[columns[0]], ax=ax)
                ax.set_title(f"Box Plot of {columns[0]}")
                ax.set_ylabel(columns[0])
                graph_description = f"A box plot for the '{columns[0]}' column was generated. It visualizes the distribution, median, quartiles, and potential outliers of this numerical feature."
            else:
                st.warning("Please specify a single numerical column for a box plot.")
                return None, "The requested box plot could not be generated. Please ensure you specify one numerical column."

        elif graph_type == "scatterplot":
            if len(columns) == 2 and pd.api.types.is_numeric_dtype(df[columns[0]]) and pd.api.types.is_numeric_dtype(df[columns[1]]):
                sns.scatterplot(x=df[columns[0]], y=df[columns[1]], ax=ax)
                ax.set_title(f"Scatter Plot of {columns[0]} vs {columns[1]}")
                ax.set_xlabel(columns[0])
                ax.set_ylabel(columns[1])
                graph_description = f"A scatter plot showing the relationship between '{columns[0]}' and '{columns[1]}' was generated. It helps visualize correlations or patterns between these two numerical features."
            else:
                st.warning("Please specify two numerical columns for a scatter plot.")
                return None, "The requested scatter plot could not be generated. Please ensure you specify two numerical columns."

        elif graph_type == "correlation_heatmap":
            numerical_cols = df.select_dtypes(include=['number']).columns
            if not numerical_cols.empty:
                corr_matrix = df[numerical_cols].corr()
                sns.heatmap(corr_matrix, annot=True, cmap='coolwarm', fmt=".2f", ax=ax)
                ax.set_title("Correlation Heatmap of Numerical Features")
                graph_description = "A correlation heatmap of all numerical features was generated. It displays the pairwise correlation coefficients, indicating the strength and direction of linear relationships between variables."
            else:
                st.warning("No numerical columns found to generate a correlation heatmap.")
                return None, "The correlation heatmap could not be generated as no numerical columns were found."
        
        elif graph_type == "bar_chart":
            if len(columns) == 1 and (pd.api.types.is_object_dtype(df[columns[0]]) or pd.api.types.is_string_dtype(df[columns[0]]) or pd.api.types.is_categorical_dtype(df[columns[0]])):
                value_counts = df[columns[0]].value_counts().head(10) # Limit to top 10 categories
                sns.barplot(x=value_counts.index, y=value_counts.values, ax=ax)
                ax.set_title(f"Bar Chart of Top Categories in {columns[0]}")
                ax.set_xlabel(columns[0])
                ax.set_ylabel("Count")
                plt.xticks(rotation=45, ha='right') # Rotate labels for readability
                plt.tight_layout()
                graph_description = f"A bar chart showing the frequency of top categories in '{columns[0]}' was generated. It helps visualize the distribution of categorical values."
            else:
                st.warning("Please specify a single categorical column for a bar chart.")
                return None, "The requested bar chart could not be generated. Please ensure you specify one categorical column."

        else:
            st.warning("Unsupported graph type requested.")
            return None, "The requested graph type is not supported. Please ask for a histogram, box plot, scatter plot, correlation heatmap, or bar chart."

        st.pyplot(fig) # Display the plot in Streamlit

        # Save plot to BytesIO for report
        img_buffer = io.BytesIO()
        fig.savefig(img_buffer, format='png', bbox_inches='tight')
        img_buffer.seek(0)
        plt.close(fig) # Close the plot to free up memory
        return img_buffer, graph_description

    except Exception as e:
        st.error(f"Error generating graph: {e}")
        plt.close(fig) # Ensure figure is closed even on error
        return None, f"An error occurred while generating the graph: {e}"

# --- Main Application Logic ---
def main_app():
    st.set_page_config(layout="wide", page_title="SSO Data Preprocessing Assistant")

    # Display Logo at the top of the main app
    st.image("SsoLogo.jpg", width=100) # Adjust width as needed
    st.title("Data Preprocessing Assistant")
    st.write(f"Welcome, {st.session_state.get('current_username', 'User')}!")


    st.sidebar.header("Upload Dataset")
    uploaded_file = st.sidebar.file_uploader("Choose a CSV or Excel file", type=["csv", "xlsx"])

    if uploaded_file is not None:
        # Check if a new file is uploaded or if df is not yet loaded
        if st.session_state['df'] is None or uploaded_file.name != st.session_state.get('uploaded_file_name'):
            st.session_state['messages'] = [] # Clear chat history for new file
            st.session_state['report_content'] = [] # Clear report content for new file
            st.session_state['user_goal'] = "Not specified" # Reset user goal
            st.session_state['uploaded_file_name'] = uploaded_file.name # Store file name to detect new upload

            try:
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file)
                elif uploaded_file.name.endswith('.xlsx'):
                    df = pd.read_excel(uploaded_file)
                st.session_state['df'] = df

                st.subheader("Dataset Preview:")
                st.dataframe(df.head())
                st.write(f"Shape: {df.shape[0]} rows, {df.shape[1]} columns")

                # Generate data summary and store it
                summary_text, summary_table = get_data_summary(df)
                st.session_state['data_summary_text'] = summary_text
                st.session_state['data_summary_table'] = summary_table

                # Add dataset overview to report content
                st.session_state['report_content'].append({"type": "heading", "level": 2, "content": "Dataset Overview"})
                st.session_state['report_content'].append({"type": "text", "content": summary_text.split("Column Details:")[0]}) # Overview text
                st.session_state['report_content'].append({"type": "table", "headers": summary_table[0], "rows": summary_table[1:]}) # Column details table

                # Initial prompt to OpenAI with data summary
                initial_ai_prompt = (
                    "Here is a detailed summary of the user's dataset:\n\n"
                    f"{st.session_state['data_summary_text']}\n\n"
                    "Based on this, what are the initial preprocessing considerations? "
                    "Please also ask the user about their primary goal (e.g., classification, regression, exploratory analysis) for this dataset."
                )
                with st.spinner("Analyzing data and generating initial insights..."):
                    initial_response = generate_openai_response(initial_ai_prompt)
                    st.session_state.messages.append({"role": "assistant", "content": initial_response})
                    st.session_state.report_content.append({"type": "heading", "level": 2, "content": "Initial Preprocessing Considerations"})
                    st.session_state.report_content.append({"type": "text", "content": initial_response})

            except Exception as e:
                st.error(f"Error reading file: {e}. Please ensure it's a valid CSV or Excel file.")
                st.session_state['df'] = None # Reset df on error
                st.stop() # Stop further execution if file reading fails

    if st.session_state['df'] is not None:
        st.subheader("Chat with your Data Preprocessing Assistant")

        # Display chat messages from history with different colors
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Chat input box - positioned before the footer
        if prompt := st.chat_input("Ask about preprocessing or analysis..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # --- Graph Generation Logic ---
            graph_generated = False
            graph_type = None
            columns_for_graph = []

            # Simple intent parsing for graph requests
            prompt_lower = prompt.lower()
            if "histogram" in prompt_lower:
                graph_type = "histogram"
                match = re.search(r"histogram of (?:the )?'?([a-zA-Z0-9_ ]+)'?", prompt_lower)
                if match:
                    col_name = match.group(1).strip()
                    if col_name in st.session_state['df'].columns:
                        columns_for_graph = [col_name]
            elif "box plot" in prompt_lower or "boxplot" in prompt_lower:
                graph_type = "boxplot"
                match = re.search(r"(?:box plot|boxplot) of (?:the )?'?([a-zA-Z0-9_ ]+)'?", prompt_lower)
                if match:
                    col_name = match.group(1).strip()
                    if col_name in st.session_state['df'].columns:
                        columns_for_graph = [col_name]
            elif "scatter plot" in prompt_lower:
                graph_type = "scatterplot"
                match = re.search(r"scatter plot of (?:the )?'?([a-zA-Z0-9_ ]+)'? vs (?:the )?'?([a-zA-Z0-9_ ]+)'?", prompt_lower)
                if match:
                    col1 = match.group(1).strip()
                    col2 = match.group(2).strip()
                    if col1 in st.session_state['df'].columns and col2 in st.session_state['df'].columns:
                        columns_for_graph = [col1, col2]
            elif "correlation heatmap" in prompt_lower or "heatmap" in prompt_lower:
                graph_type = "correlation_heatmap"
            elif "bar chart" in prompt_lower or "barplot" in prompt_lower:
                graph_type = "bar_chart"
                match = re.search(r"(?:bar chart|barplot) of (?:the )?'?([a-zA-Z0-9_ ]+)'?", prompt_lower)
                if match:
                    col_name = match.group(1).strip()
                    if col_name in st.session_state['df'].columns:
                        columns_for_graph = [col_name]

            if graph_type and (graph_type == "correlation_heatmap" or columns_for_graph):
                with st.spinner(f"Generating {graph_type.replace('_', ' ')}..."):
                    img_buffer, graph_desc = generate_and_display_graph(st.session_state['df'], graph_type, columns_for_graph)
                    if img_buffer:
                        st.session_state.report_content.append({"type": "heading", "level": 2, "content": f"Graph Generated: {graph_type.replace('_', ' ').title()}"})
                        st.session_state.report_content.append({"type": "image", "content": img_buffer, "caption": graph_desc})
                        graph_generated = True
                        
                        # Get AI interpretation of the graph
                        interpretation_prompt = (
                            f"A {graph_type.replace('_', ' ')} of the dataset was just generated. "
                            f"Here is its description: '{graph_desc}'. "
                            "Please provide a concise interpretation of what this graph tells us about the data, "
                            "especially in the context of data preprocessing. Do NOT provide code."
                        )
                        ai_interpretation = generate_openai_response(interpretation_prompt)
                        st.session_state.messages.append({"role": "assistant", "content": ai_interpretation})
                        st.session_state.report_content.append({"type": "text", "content": ai_interpretation})
                    else:
                        st.session_state.messages.append({"role": "assistant", "content": graph_desc}) # graph_desc will contain error message
                        st.session_state.report_content.append({"type": "text", "content": graph_desc})
            
            # --- General Chatbot Response (if no graph was generated) ---
            if not graph_generated:
                # Update user goal if explicitly stated in the prompt
                if "my goal is" in prompt_lower or "i want to do" in prompt_lower or "my objective is" in prompt_lower:
                    st.session_state['user_goal'] = prompt # Simple capture for now
                    st.session_state.report_content.append({"type": "heading", "level": 2, "content": "User's Stated Goal"})
                    st.session_state.report_content.append({"type": "text", "content": prompt})

                # Construct prompt for OpenAI, including data summary and full chat history
                full_prompt = (
                    f"Dataset Summary:\n{st.session_state['data_summary_text']}\n\n"
                    "Conversation History:\n" + "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state.messages]) +
                    f"\n\nUser's current message: {prompt}\n\n"
                    "Based on the dataset summary and our conversation, provide tailored preprocessing advice, "
                    "including explanations. Do NOT provide Python code snippets. "
                    "If the user has stated a goal, ensure your advice aligns with it."
                )

                with st.spinner("Generating response..."):
                    response = generate_openai_response(full_prompt)
                    st.session_state.messages.append({"role": "assistant", "content": response})
                    st.session_state.report_content.append({"type": "text", "content": response}) # Log AI responses for report
            st.rerun() # Rerun to display the new message/graph

    st.sidebar.markdown("---")
    st.sidebar.header("Report & Actions")

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
        "<div style='text-align: center;'>"
        "SSO Consultants © 2025 | All Rights Reserved."
        "</div>",
        unsafe_allow_html=True
    )

    # Logout button in sidebar (placed after footer for consistent sidebar flow)
    if st.sidebar.button("Logout"):
        st.session_state['logged_in'] = False
        st.session_state['current_username'] = None
        st.session_state['df'] = None
        st.session_state['data_summary_text'] = ""
        st.session_state['data_summary_table'] = []
        st.session_state['messages'] = []
        st.session_state['report_content'] = []
        st.session_state['user_goal'] = "Not specified"
        if 'uploaded_file_name' in st.session_state:
            del st.session_state['uploaded_file_name']
        st.rerun()

# --- Run the App ---
if not st.session_state['logged_in']:
    check_password()
else:
    main_app()

