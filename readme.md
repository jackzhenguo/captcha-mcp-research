 
 1 start mcp servers, e.g.,
 ```
 npx @playwright/mcp@latest --port 8931
 ```

 2 start the flask service；

 ```
 python app.py 
 ```

 3 now start the mcp client:

 ```
 node client.mjs 
 ```

 Before these, please install the dependencies:

 Js:

 ```
 npm i @modelcontextprotocol/sdk axios
 ```

 Python:

 ```
 pip install -r requirements.txt
 ```

 

