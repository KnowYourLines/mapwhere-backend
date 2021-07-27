[Instructions to duplicate this repository](https://docs.github.com/en/github/importing-your-projects-to-github/importing-a-repository-with-github-importer)

# Local development
Run this API with a local postgres db for data persistence. Then access from `http://localhost:8000` on a web browser. `docker-compose.yml` defines the local environment. If you want to access this API from a locally running frontend, you will need to add its URL to `CORS_ALLOWED_ORIGINS`.
```
docker-compose up
```
Stop running containers and remove images to wipe the db before testing new API changes:
```
docker-compose down --rmi all
```
To run functional tests:
```
docker exec -it channels_web_1 python manage.py test functional_tests
```
To run unit tests:
```
docker exec -it channels_web_1 python manage.py test api
```
To open a terminal on the running app:
```
docker exec -it channels_web_1 bash
```
# Deploying on Heroku
You must have [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) and the [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli#download-and-install) installed.

To create an app from the setup section defined in heroku.yml manifest, install the heroku-manifest plugin from the beta update channel:
```
heroku update beta
heroku plugins:install @heroku-cli/plugin-manifest
```
You can switch back to the stable update stream and remove the plugin at any time:
```
heroku update stable
heroku plugins:remove manifest
```
Then create your app using the --manifest flag. The stack of the app will automatically be set to container:
```
heroku create your-app-name --manifest --region eu
```
Push the code to deploy:
```
heroku git:remote -a your-app-name
git push heroku main
```
