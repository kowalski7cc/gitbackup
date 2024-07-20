#!/bin/env python3
from enum import Enum
import requests
import argparse
import re
from contextlib import closing
import secretstorage
import time
from typing import Any

def ask_confirmation(message: str, skip_confirmation: bool = False) -> bool:
        if skip_confirmation:
            return True
        response = input(f'{message} [y/N] ')
        return response.lower() == 'y'

def get_user_from_url(url) -> str:
    return url.split('/')[-2]

def is_url(url) -> bool:
    return re.match(r'^https?://', url) is not None

def string_or_url(string) -> str:
    if is_url(string):
        return get_user_from_url(string)
    else:
        return string
    
def get_repositories(owner: str) -> list[dict]:
    url = f'https://api.github.com/users/{owner}/repos'
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def json_to_clone_url(json: list[dict]) -> list[str]:
    return [repo['clone_url'] for repo in json]

def handle_response(func):
    def wrapper(*args, **kwargs):
        response: requests.Response = func(*args, **kwargs)
        if response.status_code != 200:
            try:
                return response.json()
            except:
                response.raise_for_status()
        return response.json()
    return wrapper

class GiteaClient:

    instance: str
    token: str

    def __init__(self, instance: str, token: str):
        if instance is None:
            raise ValueError('instance cannot be None')
        if not is_url(instance):
            instance = f'https://{instance}'
        self.instance = instance
        if token is None:
            raise ValueError('token cannot be None')
        self.token = token

    @handle_response
    def get_organization(self, name: str) -> dict:
        url = f'{self.instance}/api/v1/orgs/{name}'
        return requests.get(url, headers={'Authorization': f'token {self.token}'})
    
    @handle_response
    def create_organization(self, name: str, visibility: str = 'public') -> dict:
        url = f'{self.instance}/api/v1/orgs'
        return requests.post(url, headers={'Authorization': f'token {self.token}'}, json={'username': name, 'visibility': visibility})

    @handle_response
    def get_repository(self, owner: str, name: str) -> dict:
        url = f'{self.instance}/api/v1/repos/{owner}/{name}'
        return requests.get(url, headers={'Authorization': f'token {self.token}'})
    
    @handle_response
    def get_user(self) -> dict:
        url = f'{self.instance}/api/v1/user'
        return requests.get(url, headers={'Authorization': f'token {self.token}'})

    @handle_response
    def migrate_repository(self, clone_addr: str, owner: str, name: str, private: bool = False, mirror: bool = False, wiki: bool = False) -> dict:
        url = f'{self.instance}/api/v1/repos/migrate'
        return requests.post(url, headers={'Authorization': f'token {self.token}'}, json={'clone_addr': clone_addr, 'repo_name': name, 'repo_owner': owner, 'mirror': mirror, 'private': private, 'wiki': wiki})
    

def main():
    parser = argparse.ArgumentParser(prog="gitbackup", description="Backup all repositories from a GitHub organization or user to Gitea")
    parser.add_argument("repository", help="The repository to backup")
    parser.add_argument("-m", "--mirror", help="Keep the repository in sync with the original", action="store_true")
    parser.add_argument("-w", "--wiki", help="Backup the wiki", action="store_true")
    parser.add_argument("-y", "--yes", help="Skip confirmation", action="store_true")
    group_privacy = parser.add_mutually_exclusive_group()
    group_privacy.add_argument("-i", "--internal", help="Make the repositories internal", action="store_true")
    group_privacy.add_argument("-p", "--private", help="Make the repositories private", action="store_true")
    group_filters = parser.add_mutually_exclusive_group()
    group_filters.add_argument("--include", help="Include only repositories that match the given regex")
    group_filters.add_argument("--exclude", help="Exclude repositories that match the given regex")
    parser.add_argument("-o", "--organization", help="The new name of the organization in gitea")
    args = parser.parse_args()

    credentials = None
    
    with closing(secretstorage.dbus_init()) as connection:
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            collection.unlock()
        credentials = next(collection.search_items({'application': 'gitbackup'}), None)

        if credentials is None:
            instance_url = input('Insert your instance URL: ')
            if instance_url == '':
                print('Instance URL cannot be empty')
                return
            import urllib.parse
            instance_url = urllib.parse.urlparse(instance_url)
            instance = f'{instance_url.scheme}://{instance_url.netloc}'
            token = input('Insert your token: ')
            if token == '':
                print('Token cannot be empty')
                return
            credentials = collection.create_item('gitbackup', {'instance': instance, 'application': 'gitbackup'}, token.encode('utf-8'))

        instance = credentials.get_attributes()['instance']
        token = credentials.get_secret().decode('utf-8')
            

    user = string_or_url(args.repository)

    print(f'Backing up {user}\'s repositories...')

    repolist = get_repositories(user)
    print(f'Got {len(repolist)} repositories')

    gitea = GiteaClient(instance, token)
    gitea_org = args.organization if args.organization is not None else user
    print('Checking if organization exists in gitea...')
    if 'id' in gitea.get_organization(gitea_org):
        print(f'WARNING: Organization {gitea_org} exists in gitea')
        if not ask_confirmation('Clone repositories in existing organization?', args.yes):
            print('Aborting...')
            return
    else:
        print(f'Creating organization {gitea_org} in gitea...')
        result = gitea.create_organization(gitea_org, 'private' if args.private or args.internal else 'public')
        if 'message' in result:
            print(f'Error: {result["message"]}')
            return

    print('Cloning repositories...')
    for index, repo in enumerate(repolist):
        repo_name, repo_clone = repo['name'], repo['clone_url']
        print(f'Cloning {repo_name} ({index + 1}/{len(repolist)})...')
        if args.include is not None and not re.match(args.include, repo_name):
            print(f'Repository {repo_name} does not match include filter, skipping...')
            continue
        if args.exclude is not None and re.match(args.exclude, repo_name):
            print(f'Repository {repo_name} matches exclude filter, skipping...')
            continue
        if 'id' in gitea.get_repository(gitea_org, repo_name) :
            print(f'Repository {repo_name} exists in gitea, skipping...')
            continue
        result = gitea.migrate_repository(repo_clone, gitea_org, repo_name, args.private, args.mirror, args.wiki)
        if 'message' in result:
            print(f'Error: {result["message"]}')
            return
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
