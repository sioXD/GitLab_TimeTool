from Workitem import Workitem
import datetime as dt


class Issue(Workitem):
    type="issue"
    def __init__(self, title, id):
        super().__init__(title, id)
        self.userTimeMap = {}

    def addTimeSpentByUser(self,time,user,date):
        """UserTimeMap ist ein Diktionary mit einem Eintrag pro User 
        und im Eintrag für einen User ist eine Liste von Objekten, die einen Zeitaufwand
        und ein Datum enthalten"""
        try:
            if user in self.userTimeMap.keys():
                self.userTimeMap[user].append({
                        'Zeit(Std)':time,
                        'Datum':date
                    })
            else:
                self.userTimeMap[user] = [{
                        'Zeit(Std)':time,
                        'Datum':date
                    }]
        except:
            self.userTimeMap = {user:[{
                'Zeit(Std)':time,
                'Datum':date
            }]
            }
     
            

    def addLabel(self,label):
        """Füge ein label der Liste von Labels hinzu"""
        try:
            self.labels.append(label)
        except:
            self.labels = [label]
        

    def hasLabel(self,label):
        try:
            return label in self.labels
        except:
            return False
        
    def getLabels(self):
        try:
            return self.labels
        except:
            return []
    def getUserTimesDated(self,user):
        try:
            return self.userTimeMap[user]
        except:
            return []
    def getUserTotalTime(self,user):
        try:
            return sum([time['Zeit(Std)'] for time in self.userTimeMap[user]])
        except:
            return 0

    def getUserPercentagesByTime(self):
        
        try:
            if self.hoursSpent < 0.001 and len(self.userTimeMap)==0:
                return {}
            userTimes = {}
            for user in self.userTimeMap:
                userTimes[user] = self.getUserTotalTime(user)
            self.hoursSpent = sum((userTimes[user] for user in userTimes ))
            for user in self.userTimeMap:
                userTimes[user] /= self.hoursSpent
            return userTimes
        except:
            return {}
        
    if __name__ == "__main__":
        from Issue import Issue

        isu = Issue("Hello",1)
        isu.addLabel("Pronto")
        isu.addLabel("LOL")
        isu.addTimeSpentByUser(0.5,"Nivek",dt.datetime(2025,10,16))
        print(isu.userTimeMap)
        isu.addTimeSpentByUser(2.5,"Nivek",dt.datetime(2025,10,26))
        print(isu.userTimeMap)
        isu.addTimeSpentByUser(0.5,"Bürek",dt.datetime(2025,11,16))
        print(isu.userTimeMap)
        print(isu.getUserPercentagesByTime())